"""
GrannyDaemon — factory polling loop.

Polls cc_queue every POLL_INTERVAL_SEC for sprint-ready tickets, routes each
via GrannyWeatherwaxDevice.route_ticket(), and dispatches CC tickets via the
cc_dispatch_fn (which posts GRANNY_DISPATCH to the shared channel).

Designed to run as a background thread (started from GrannyShim.start()) or
as a standalone process (python -m devices.granny.daemon).

Lifecycle:
  daemon = GrannyDaemon()
  daemon.start()   # spawns daemon thread
  daemon.stop()    # signals thread to exit

Deduplication: ticket ids dispatched in the current poll window are tracked
in _dispatched_this_cycle. The set is cleared on each new poll cycle so
re-queue is possible after a ticket is closed and re-opened.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

from bus.envelope import Envelope
from bus.imap_server import IMAPServer

log = logging.getLogger(__name__)

POLL_INTERVAL_SEC = int(os.environ.get("GRANNY_POLL_INTERVAL", "60"))
_UU_ROOT = Path(__file__).parent.parent.parent.resolve()
# Always use UU's own cc_queue.py — never inherited CC_WORKFLOW_TOOLS.
_CC_QUEUE = _UU_ROOT / "lab" / "claudecode" / "cc_queue.py"
_PYTHON = sys.executable

_UC_PORT = int(os.environ.get("IGOR_UC_PORT", "8082"))
_UC_BASE = os.environ.get("IGOR_UC_BASE", f"http://localhost:{_UC_PORT}")


def _post_rack(path: str, body: dict, timeout: float = 3.0) -> bool:
    """POST JSON to rack server. Returns True on success, False on any failure."""
    try:
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{_UC_BASE}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status < 300
    except Exception:
        return False


# Tags that Granny routes to CC by default (mirrors _DEFAULT_ROUTING cc paths)
_CC_TAGS = frozenset(
    {"Platform", "Infrastructure", "Cognition", "Database", "Training", "Research"}
)
# Statuses that indicate a ticket is ready to dispatch
_DISPATCHABLE_STATUSES = {"sprint"}
# Statuses that mean already handled — skip
_SKIP_STATUSES = {"in_progress", "done", "closed", "awaiting_validation"}


def _load_sprint_tickets() -> list[dict]:
    """Load tickets with status=sprint from cc_queue. Returns [] on error.

    cc_queue.py list has no --json flag; we parse its text output to extract
    ticket IDs then call show <id> for full JSON per ticket.
    """
    try:
        list_result = subprocess.run(
            [str(_PYTHON), str(_CC_QUEUE), "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if list_result.returncode != 0:
            log.warning(
                "GrannyDaemon: cc_queue list failed: %s", list_result.stderr[:200]
            )
            return []

        # Extract ticket IDs from formatted output: "  ⬜ [T-foo-bar] (S) ..."
        ids = re.findall(r"\[(T-[a-z0-9-]+)\]", list_result.stdout)

        tickets = []
        for tid in ids:
            show_result = subprocess.run(
                [str(_PYTHON), str(_CC_QUEUE), "show", tid],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if show_result.returncode != 0:
                continue
            try:
                t = json.loads(show_result.stdout)
                if t.get("status") in _DISPATCHABLE_STATUSES and not t.get("gate"):
                    tickets.append(t)
            except json.JSONDecodeError:
                pass
        return tickets
    except Exception as e:
        log.warning("GrannyDaemon: failed to load tickets: %s", e)
        return []


def _ticket_needs_cc(ticket: dict) -> bool:
    """Return True if this ticket should be routed to CC."""
    worker = ticket.get("worker", "")
    if worker == "claude":
        return True
    if worker and worker != "cc":
        return False  # explicitly assigned to someone else
    # No explicit worker — check tags
    tags = set(ticket.get("tags", []))
    return bool(tags & _CC_TAGS)


class GrannyDaemon:
    """Background polling daemon that routes sprint-ready tickets to workers."""

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._dispatched_ids: set[str] = set()
        self._total_dispatched: int = 0
        self._total_errors: int = 0
        self._last_poll: Optional[float] = None

        # Build device with CC dispatch wired
        from devices.granny.device import GrannyWeatherwaxDevice
        from devices.granny.dispatch import cc_dispatch_fn

        self._device = GrannyWeatherwaxDevice()
        self._device.register_worker(
            "cc",
            list(_CC_TAGS),
            dispatch_fn=cc_dispatch_fn,
        )

        self._alerted_ids: set[str] = set()
        try:
            self._imap: Optional[IMAPServer] = IMAPServer()
            self._imap.start()
            self._imap.create_mailbox("CC.0")
        except Exception as e:
            log.warning("GrannyDaemon: IMAP setup failed — CC alerts disabled: %s", e)
            self._imap = None

    def start(self) -> None:
        """Start the polling daemon in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, name="granny-daemon", daemon=True
        )
        self._thread.start()
        log.info("GrannyDaemon: started (poll_interval=%ds)", POLL_INTERVAL_SEC)
        self._post_channel(
            "Granny Weatherwax daemon started — watching for sprint tickets."
        )
        _post_rack(
            "/api/agents/register",
            {
                "agent_id": "granny-weatherwax",
                "capabilities": ["intake_ticket", "route_ticket", "cc_dispatch"],
                "tmux_target": "granny",
            },
        )

    def stop(self) -> None:
        """Signal daemon to stop and wait for thread to exit."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        _post_rack("/api/agents/deregister", {"agent_id": "granny-weatherwax"})
        self._post_channel("Granny Weatherwax daemon stopped.")
        log.info("GrannyDaemon: stopped")

    def is_running(self) -> bool:
        """Return True if the daemon thread is alive."""
        return bool(self._thread and self._thread.is_alive())

    def run_once(self) -> int:
        """Run one poll cycle. Returns count of tickets dispatched. Testable without threads."""
        tickets = _load_sprint_tickets()
        dispatched = 0
        new_ids: set[str] = set()

        for ticket in tickets:
            tid = ticket.get("id", "")
            if not tid:
                continue
            if tid in self._dispatched_ids:
                log.debug("GrannyDaemon: skipping already-dispatched %s", tid)
                continue
            if not _ticket_needs_cc(ticket):
                log.debug("GrannyDaemon: %s not a CC ticket — skip", tid)
                continue

            audit = self._device.intake_ticket(ticket)
            if not audit.passed and not audit.escalate_to_cc:
                log.warning("GrannyDaemon: %s failed audit — %s", tid, audit.reasons)
                self._alert_cc(tid, str(audit.reasons), "audit_fail")
                continue

            ok, worker_id = self._device.route_ticket(ticket)
            if ok:
                new_ids.add(tid)
                dispatched += 1
                self._total_dispatched += 1
                log.info("GrannyDaemon: dispatched %s → %s", tid, worker_id)
            else:
                self._total_errors += 1
                log.warning(
                    "GrannyDaemon: route failed for %s (worker=%s)", tid, worker_id
                )
                self._alert_cc(tid, f"route failed, worker={worker_id}", "route_fail")

        self._dispatched_ids = new_ids  # reset to only current-cycle dispatches
        self._last_poll = time.time()
        return dispatched

    def _push_stats(self) -> None:
        """Push current stats to the rack server dashboard (best-effort)."""
        _post_rack(
            "/api/agents/granny-weatherwax/stats",
            {
                "status": "running",
                "total_dispatched": self._total_dispatched,
                "total_errors": self._total_errors,
                "poll_interval_sec": POLL_INTERVAL_SEC,
                "last_poll": self._last_poll,
                "dispatched_this_cycle": len(self._dispatched_ids),
            },
        )

    def _run(self) -> None:
        """Main daemon loop — polls until stop_event set."""
        while not self._stop_event.is_set():
            try:
                n = self.run_once()
                if n:
                    log.info("GrannyDaemon: poll cycle — %d ticket(s) dispatched", n)
                self._push_stats()
            except Exception as e:
                self._total_errors += 1
                log.error("GrannyDaemon: poll cycle error: %s", e)
                self._alert_cc("__cycle__", str(e), "poll_error")
            self._stop_event.wait(timeout=POLL_INTERVAL_SEC)

    def _alert_cc(self, ticket_id: str, reason: str, kind: str) -> None:
        """Send a one-shot alert to CC.0 on unresolvable issues. Deduped per ticket+kind."""
        dedup_key = f"{ticket_id}:{kind}"
        if dedup_key in self._alerted_ids:
            return
        if self._imap is None:
            log.debug(
                "GrannyDaemon: _alert_cc skipped (IMAP not available): %s %s",
                ticket_id,
                kind,
            )
            return
        try:
            envelope = Envelope.now(
                "Granny.0",
                "CC.0",
                {"ticket_id": ticket_id, "kind": kind, "reason": reason},
            )
            self._imap.append("CC.0", envelope)
            self._alerted_ids.add(dedup_key)
            log.info("GrannyDaemon: alerted CC.0 — %s %s", ticket_id, kind)
        except Exception as e:
            log.warning(
                "GrannyDaemon: CC alert failed for %s (%s): %s", ticket_id, kind, e
            )

    def _post_channel(self, msg: str) -> None:
        try:
            from unseen_university.channel import post_to_channel

            post_to_channel(msg, author="granny-weatherwax", channel="shared")
        except Exception as e:
            log.warning("GrannyDaemon: channel post failed: %s", e)


# ── Singleton ─────────────────────────────────────────────────────────────────

_daemon: Optional[GrannyDaemon] = None


def get_daemon() -> GrannyDaemon:
    """Return (or create) the singleton GrannyDaemon."""
    global _daemon
    if _daemon is None:
        _daemon = GrannyDaemon()
    return _daemon


# ── __main__ entry ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import signal
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    daemon = get_daemon()
    daemon.start()

    def _handle_sig(sig, _frame):
        log.info("GrannyDaemon: received signal %s — shutting down", sig)
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)

    # Block main thread
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        daemon.stop()
