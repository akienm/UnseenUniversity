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
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

POLL_INTERVAL_SEC = int(os.environ.get("GRANNY_POLL_INTERVAL", "60"))
_CC_QUEUE = (
    Path(os.environ.get("CC_WORKFLOW_TOOLS", Path.home() / "TheIgors/lab/claudecode"))
    / "cc_queue.py"
)

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
            ["python3", str(_CC_QUEUE), "list"],
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
                ["python3", str(_CC_QUEUE), "show", tid],
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

        # Build device with CC dispatch wired
        from devices.granny.device import GrannyWeatherwaxDevice
        from devices.granny.dispatch import cc_dispatch_fn

        self._device = GrannyWeatherwaxDevice()
        self._device.register_worker(
            "cc",
            list(_CC_TAGS),
            dispatch_fn=cc_dispatch_fn,
        )

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

    def stop(self) -> None:
        """Signal daemon to stop and wait for thread to exit."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._post_channel("Granny Weatherwax daemon stopped.")
        log.info("GrannyDaemon: stopped")

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
                continue

            ok, worker_id = self._device.route_ticket(ticket)
            if ok:
                new_ids.add(tid)
                dispatched += 1
                log.info("GrannyDaemon: dispatched %s → %s", tid, worker_id)
            else:
                log.warning(
                    "GrannyDaemon: route failed for %s (worker=%s)", tid, worker_id
                )

        self._dispatched_ids = new_ids  # reset to only current-cycle dispatches
        return dispatched

    def _run(self) -> None:
        """Main daemon loop — polls until stop_event set."""
        while not self._stop_event.is_set():
            try:
                n = self.run_once()
                if n:
                    log.info("GrannyDaemon: poll cycle — %d ticket(s) dispatched", n)
            except Exception as e:
                log.error("GrannyDaemon: poll cycle error: %s", e)
            self._stop_event.wait(timeout=POLL_INTERVAL_SEC)

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
