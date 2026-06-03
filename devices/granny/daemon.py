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
MAX_CONCURRENT_CC = int(os.environ.get("GRANNY_MAX_CC", "1"))
# How often to run the orphan watchdog (in poll cycles; default every 10 cycles = 10 min)
_ORPHAN_CHECK_EVERY_N_CYCLES = int(os.environ.get("GRANNY_ORPHAN_CHECK_CYCLES", "10"))
_USAGE_CACHE = Path.home() / ".claude" / "usage-cache.json"
_RATE_LIMIT_PAUSE_PCT = float(os.environ.get("GRANNY_RATE_LIMIT_PAUSE", "90"))
_RATE_LIMIT_7D_PAUSE_PCT = float(os.environ.get("GRANNY_RATE_LIMIT_7D_PAUSE", "90"))
_UU_ROOT = Path(__file__).parent.parent.parent.resolve()
# Always use UU's own cc_queue.py — never inherited CC_WORKFLOW_TOOLS.
_CC_QUEUE = _UU_ROOT / "lab" / "claudecode" / "cc_queue.py"
_PYTHON = sys.executable

_UC_PORT = int(os.environ.get("IGOR_UC_PORT", "8082"))
_UC_BASE = os.environ.get("IGOR_UC_BASE", f"http://localhost:{_UC_PORT}")

_GRANNY_HOME = Path(os.environ.get("GRANNY_HOME", str(Path.home() / ".granny")))
_GRANNY_PID_FILE = _GRANNY_HOME / "daemon.pid"
_DISPATCHED_CYCLE_FILE = _GRANNY_HOME / "dispatched_cycle.json"
_STATS_CHANNEL_POST = "GRANNY_STATS"


def daemon_pid_file() -> Path:
    """Return the PID file path for the GrannyDaemon standalone process."""
    return _GRANNY_PID_FILE


def _load_dispatched_ids() -> set[str]:
    """Read dispatched_cycle.json. Returns empty set on missing/corrupt file."""
    try:
        if _DISPATCHED_CYCLE_FILE.exists():
            data = json.loads(_DISPATCHED_CYCLE_FILE.read_text())
            return set(data.get("ids", []))
    except Exception as e:
        log.debug("_load_dispatched_ids: %s", e)
    return set()


def _save_dispatched_ids(ids: set[str]) -> None:
    """Persist dispatched_cycle ids to disk. Best-effort — never raises."""
    try:
        _DISPATCHED_CYCLE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _DISPATCHED_CYCLE_FILE.write_text(json.dumps({"ids": list(ids)}))
    except Exception as e:
        log.debug("_save_dispatched_ids: %s", e)


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


# Tags that Granny routes to CC — broad catch-all; only 'minion'-tagged tickets
# go to cheap inference workers instead. Add tags here as new topic areas emerge.
_CC_TAGS = frozenset(
    {
        "Platform",
        "Infrastructure",
        "Cognition",
        "Database",
        "Training",
        "Research",
        "Memory",
        "Architecture",
        "Device",
        "Inference",
        "CompiledInference",
        "Archivist",
        "Librarian",
        "Scraps",
        "Clan",
        "Palace",
        "Concepts",
        "GoogleSecretary",
        "SWADL",
        "Security",
        "Test",
        "Workflow",
        "Consequence",
        "Performance",
        "Migration",
        "Cleanup",
        "Queue",
        "ADC",
        "AgentDatacenter",
        "Igor",
        "Observability",
        "Registry",
        "Skeleton",
    }
)
# Statuses that indicate a ticket is ready to dispatch
_DISPATCHABLE_STATUSES = {"sprint"}
# Statuses that mean already handled — skip
_SKIP_STATUSES = {"in_progress", "done", "closed", "awaiting_validation"}


def _load_sprint_tickets() -> list[dict]:
    """Load tickets with status=sprint directly from Postgres. Returns [] on error.

    Queries clan.memories directly — avoids O(N) subprocess calls on a large queue.
    """
    try:
        import psycopg2
        import psycopg2.extras

        db_url = os.environ.get(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
        conn = psycopg2.connect(db_url, connect_timeout=5)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""SELECT metadata FROM clan.memories
                   WHERE metadata->>'kind' = 'ticket'
                   AND metadata->>'status' = 'sprint'
                   AND (metadata->>'gate' IS NULL OR metadata->>'gate' = '')
                   ORDER BY (metadata->>'priority')::float DESC NULLS LAST
                   LIMIT 50""")
            rows = cur.fetchall()
        conn.close()
        return [dict(r["metadata"]) for r in rows]
    except Exception as e:
        log.warning("GrannyDaemon: failed to load tickets: %s", e)
        return []


def _check_rate_limit() -> tuple[bool, Optional[str], float]:
    """Read usage cache. Returns (ok_to_dispatch, signal, pct). signal names which limit fired.

    Also checks:
    - Cache staleness: if cache is >5 min old and workers are active, treat as high usage.
    - Extra usage disabled: if overage buffer is gone, lower effective pause threshold to 80%.
    """
    try:
        import datetime

        data = json.loads(_USAGE_CACHE.read_text())

        # Staleness check — cache only updates on CC stop events
        updated_at = data.get("updatedAt", "")
        if updated_at:
            age_seconds = (
                datetime.datetime.now(datetime.timezone.utc)
                - datetime.datetime.fromisoformat(updated_at)
            ).total_seconds()
            active = _count_active_cc_sessions()
            if age_seconds > 300 and active > 0:
                log.warning(
                    "GrannyDaemon: usage cache is %.0fs old with %d active workers — assuming high usage",
                    age_seconds,
                    active,
                )
                return (False, "stale-cache", 0.0)

        usage = data.get("usage", {})

        # Extra usage disabled means no buffer — be more conservative (80% threshold)
        extra = usage.get("extra_usage") or {}
        buffer_gone = bool(extra.get("disabled_reason"))
        effective_pause = 80.0 if buffer_gone else _RATE_LIMIT_PAUSE_PCT

        five_pct = (usage.get("five_hour") or {}).get("utilization") or 0.0
        if five_pct >= effective_pause:
            signal = "5h-no-buffer" if buffer_gone else "5h"
            return (False, signal, five_pct)
        seven_pct = (usage.get("seven_day") or {}).get("utilization") or 0.0
        if seven_pct >= _RATE_LIMIT_7D_PAUSE_PCT:
            return (False, "7d", seven_pct)
        return (True, None, five_pct)
    except Exception:
        return (True, None, 0.0)


def _cc0_in_progress() -> bool:
    """Return True when a worker=claude ticket is currently in_progress (CC.0 is busy)."""
    try:
        import psycopg2

        db_url = os.environ.get(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
        )
        conn = psycopg2.connect(db_url, connect_timeout=5)
        with conn.cursor() as cur:
            cur.execute("""SELECT 1 FROM clan.memories
                   WHERE metadata->>'kind' = 'ticket'
                   AND metadata->>'status' = 'in_progress'
                   AND metadata->>'worker' IN ('claude', 'cc')
                   LIMIT 1""")
            row = cur.fetchone()
        conn.close()
        return row is not None
    except Exception as e:
        log.debug("_cc0_in_progress: query failed (assuming not busy): %s", e)
        return False


_MINION_TAGS = frozenset({"minion"})


def _ticket_is_minion(ticket: dict) -> bool:
    """Return True if this ticket should be routed to a cheap minion model."""
    tags = set(ticket.get("tags", []))
    return bool(tags & _MINION_TAGS)


def _ticket_needs_cc(ticket: dict) -> bool:
    """Return True if this ticket must be routed to Claude CC.

    Igor coding is retired — worker=igor is treated as worker=claude.
    Only 'minion'-tagged tickets route to cheap inference workers.
    Everything else (worker=claude, worker=igor, worker unset) → CC.
    """
    tags = set(ticket.get("tags") or [])
    return "minion" not in tags


def _cc0_available() -> bool:
    """Return True when all CC.0 dispatch gates pass.

    Gates checked:
      1. CC.0 in granny.yaml config
      2. time_window, semaphore, usage_max_pct gates from granny.yaml
      3. cc_concurrency_mode from cc.yaml: cc0_only → block when another CC is in_progress
    """
    try:
        from devices.granny.dispatch_config import (
            evaluate_worker_gates,
            get_cc_concurrency_mode,
            get_worker_config,
            load_dispatch_config,
        )
        from datetime import datetime

        config = load_dispatch_config()
        cc0_config = get_worker_config("CC.0", config)
        if not cc0_config:
            log.debug("_cc0_available: CC.0 not in granny.yaml config")
            return False

        ctx = {
            "now": datetime.now(),
            "usage_pct": _get_usage_pct(),
            "cc0_busy": False,  # max_concurrent gate removed from granny.yaml; handled below
        }

        if not evaluate_worker_gates("CC.0", cc0_config, ctx):
            log.debug("_cc0_available: gates failed for CC.0")
            return False

        # Concurrency check derived from cc.yaml cc_concurrency_mode.
        # cc0_only → block dispatch when any CC session is already in_progress.
        mode = get_cc_concurrency_mode()
        if mode == "cc0_only" and _cc0_in_progress():
            log.debug("_cc0_available: cc0_only mode — another CC session already in_progress")
            return False

        log.debug("_cc0_available: gates passed for CC.0 (mode=%s)", mode)
        return True
    except Exception as e:
        log.debug("_cc0_available: gate check failed: %s", e)
        return False


def _dicksimnel_available() -> bool:
    """Return True when DickSimnel.0's availability flag is set and not blocked.

    Semaphore protocol:
      ~/.granny/available/DickSimnel.0.available.true  → present = available
      ~/.granny/available/DickSimnel.0.available.false → present = unavailable (wins)
    """
    flag_dir = Path.home() / ".granny" / "available"
    true_flag = flag_dir / "DickSimnel.0.available.true"
    false_flag = flag_dir / "DickSimnel.0.available.false"
    if false_flag.exists():
        return False
    return true_flag.exists()


def _get_usage_pct() -> float:
    """Get Claude's 5-hour usage percentage from cache (set by shim). Default 0.0."""
    try:
        import json

        usage_cache = _USAGE_CACHE
        if usage_cache.exists():
            data = json.loads(usage_cache.read_text())
            pct = float(data.get("usage_pct", 0.0))
            return pct
    except Exception as e:
        log.debug("_get_usage_pct: failed to read cache: %s", e)
    return 0.0


class GrannyDaemon:
    """Background polling daemon that routes sprint-ready tickets to workers."""

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._dispatched_ids: set[str] = _load_dispatched_ids()
        self._start_time: Optional[float] = None
        self._total_dispatched: int = 0
        self._total_errors: int = 0
        self._last_poll: Optional[float] = None
        self._task_listener: Optional[object] = None

        # Build device with CC dispatch wired; store inference dispatch fn separately
        from devices.granny.device import GrannyWeatherwaxDevice
        from devices.granny.dispatch import cc_dispatch_fn, inference_dispatch_fn

        from devices.granny.pattern_tracker import PatternTracker

        self._device = GrannyWeatherwaxDevice()
        self._device.register_worker(
            "cc",
            list(_CC_TAGS),
            dispatch_fn=cc_dispatch_fn,
        )
        self._inference_dispatch = inference_dispatch_fn
        self._pattern_tracker = PatternTracker()

        self._alerted_ids: set[str] = set()
        try:
            self._imap: Optional[IMAPServer] = IMAPServer()
            self._imap.start()
            self._imap.create_mailbox("CC.0")
            self._imap.create_mailbox("feeds/granny")
        except Exception as e:
            log.warning("GrannyDaemon: IMAP setup failed — CC alerts disabled: %s", e)
            self._imap = None

    def start(self) -> None:
        """Start the polling daemon in a background thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._start_time = time.time()
        self._thread = threading.Thread(
            target=self._run, name="granny-daemon", daemon=True
        )
        self._thread.start()
        log.info("GrannyDaemon: started (poll_interval=%ds)", POLL_INTERVAL_SEC)
        self._start_task_listener()
        self._post_channel(
            f"{_STATS_CHANNEL_POST}|event=start|total_dispatched={self._total_dispatched}"
            f"|total_errors={self._total_errors}|dispatched_ids={len(self._dispatched_ids)}"
        )
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

    def _start_task_listener(self) -> None:
        try:
            from lab.claudecode.cc_task_listener import TaskListener

            self._task_listener = TaskListener()
            t = threading.Thread(
                target=self._task_listener.run,
                name="cc-task-listener",
                daemon=True,
            )
            t.start()
            log.info("GrannyDaemon: cc_task_listener started")
        except Exception as e:
            log.warning("GrannyDaemon: cc_task_listener failed to start: %s", e)

    def stop(self) -> None:
        """Signal daemon to stop and wait for thread to exit."""
        self._stop_event.set()
        if self._task_listener:
            try:
                self._task_listener.stop()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=5)
        uptime = int(time.time() - self._start_time) if self._start_time else 0
        _post_rack("/api/agents/deregister", {"agent_id": "granny-weatherwax"})
        self._post_channel(
            f"{_STATS_CHANNEL_POST}|event=stop|total_dispatched={self._total_dispatched}"
            f"|total_errors={self._total_errors}|uptime_sec={uptime}"
        )
        self._post_channel("Granny Weatherwax daemon stopped.")
        log.info("GrannyDaemon: stopped")

    def is_running(self) -> bool:
        """Return True if the daemon thread is alive."""
        return bool(self._thread and self._thread.is_alive())

    def run_once(self) -> int:
        """Run one poll cycle. Returns count of tickets dispatched. Testable without threads."""
        ok, signal, rate_pct = _check_rate_limit()
        if not ok:
            threshold = (
                _RATE_LIMIT_PAUSE_PCT if signal == "5h" else _RATE_LIMIT_7D_PAUSE_PCT
            )
            log.warning(
                "GrannyDaemon: %s rate limit at %.0f%% — pausing until below %d%%",
                signal,
                rate_pct,
                threshold,
            )
            return 0

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
            if _ticket_needs_cc(ticket):
                # Priority: CC.0 → DickSimnel.0 → audit+OR cascade
                if _cc0_available():
                    from devices.granny.dispatch import cc0_dispatch_fn

                    ok = cc0_dispatch_fn(ticket, session="claude-main")
                    worker_id = "cc-0"
                elif _dicksimnel_available():
                    # DickSimnel.0: OR-powered worker tier.
                    # Does NOT handle HIGH-inertia tickets — those wait for CC.
                    audit = self._device.intake_ticket(ticket)
                    if audit.escalate_to_cc:
                        self._hold_for_cc_approval(tid, str(audit.reasons))
                        ok = True
                        worker_id = "hold-for-cc"
                    elif audit.passed:
                        from devices.granny.dispatch import dicksimnel_dispatch_fn

                        ok = dicksimnel_dispatch_fn(ticket)
                        worker_id = "dicksimnel"
                    else:
                        self._hold_for_audit_fail(tid, audit.reasons)
                        continue
                else:
                    # Fall back to audit + OR cascade
                    audit = self._device.intake_ticket(ticket)
                    if not audit.passed and not audit.escalate_to_cc:
                        self._hold_for_audit_fail(tid, audit.reasons)
                        continue
                    if audit.escalate_to_cc:
                        # HIGH-inertia: block and alert CC.0 for human approval.
                        # Never auto-dispatch.
                        self._hold_for_cc_approval(tid, str(audit.reasons))
                        ok = True
                        worker_id = "hold-for-cc"
                    else:
                        # Audit passed: try OR cascade (analyst→worker→minion).
                        # Only blocks for CC if all OR tiers ESCALATE.
                        ok = self._inference_dispatch(
                            ticket, on_complete=self._record_inference_outcome
                        )
                        worker_id = "or-cascade"
            else:
                # minion-tagged tickets: skip directly to minion tier in cascade.
                ok = self._inference_dispatch(
                    ticket, on_complete=self._record_inference_outcome
                )
                worker_id = "or-minion"

            if ok:
                new_ids.add(tid)
                dispatched += 1
                self._total_dispatched += 1
                log.info("GrannyDaemon: dispatched %s → %s", tid, worker_id)
                self._publish_feed("dispatch", tid, f"dispatched to {worker_id}")
            else:
                self._total_errors += 1
                log.warning(
                    "GrannyDaemon: route failed for %s (worker=%s)", tid, worker_id
                )
                self._alert_cc(tid, f"route failed, worker={worker_id}", "route_fail")
                self._publish_feed(
                    "route_fail", tid, f"route failed, worker={worker_id}"
                )

        self._dispatched_ids |= new_ids  # accumulate — holds from prior cycles must not re-escalate
        _save_dispatched_ids(self._dispatched_ids)
        self._last_poll = time.time()
        return dispatched

    def _record_inference_outcome(
        self, worker_result, task_class: str, ticket: dict
    ) -> None:
        """on_complete callback — records WorkerResult into PatternTracker and escalation corpus."""
        self._pattern_tracker.record(
            ticket_id=ticket.get("id", ""),
            tags=list(ticket.get("tags", [])),
            task_class=task_class,
            size=ticket.get("size", "?"),
            signal=worker_result.signal,
            iterations=worker_result.iterations,
            cost_usd=worker_result.cost_usd,
            advisor_signal=worker_result.advisor_signal,
        )
        # Append non-DONE outcomes to escalation corpus for routing compiler analysis
        from devices.granny.escalation_corpus import append_outcome

        append_outcome(
            ticket,
            signal=worker_result.signal,
            advisor_signal=worker_result.advisor_signal,
            task_class=task_class,
            iterations=worker_result.iterations,
            cost_usd=worker_result.cost_usd,
            tokens_in=worker_result.input_tokens,
            tokens_out=worker_result.output_tokens,
            excerpt=worker_result.notes,
        )
        if self._pattern_tracker.should_report():
            report = self._pattern_tracker.format_report()
            log.info("GrannyDaemon: %s", report)
            self._post_channel(report)

    def _push_stats(self) -> None:
        """Push current stats to the rack server dashboard (best-effort)."""
        _post_rack(
            "/api/agents/granny-weatherwax/stats",
            {
                "status": "running",
                "total_dispatched": self._total_dispatched,
                "total_errors": self._total_errors,
                "poll_interval_sec": POLL_INTERVAL_SEC,
                "cc0_in_progress": _cc0_in_progress(),
                "last_poll": self._last_poll,
                "dispatched_this_cycle": len(self._dispatched_ids),
            },
        )

    def _run(self) -> None:
        """Main daemon loop — polls until stop_event set."""
        cycle = 0
        while not self._stop_event.is_set():
            try:
                n = self.run_once()
                if n:
                    log.info("GrannyDaemon: poll cycle — %d ticket(s) dispatched", n)
                self._push_stats()
                cycle += 1
                if cycle % _ORPHAN_CHECK_EVERY_N_CYCLES == 0:
                    self._run_orphan_watchdog()
            except Exception as e:
                self._total_errors += 1
                log.error("GrannyDaemon: poll cycle error: %s", e)
                self._alert_cc("__cycle__", str(e), "poll_error")
            self._stop_event.wait(timeout=POLL_INTERVAL_SEC)

    def _hold_for_audit_fail(self, ticket_id: str, reasons) -> None:
        """Block ticket on audit failure and alert CC.0 so the description gets fixed."""
        reasons_str = "; ".join(reasons) if isinstance(reasons, list) else str(reasons)
        hold_reason = f"audit fail: {reasons_str[:300]}"
        log.warning("GrannyDaemon: %s audit fail — %s", ticket_id, reasons_str)
        try:
            subprocess.run(
                [_PYTHON, str(_CC_QUEUE), "block", ticket_id, hold_reason],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as e:
            log.warning("GrannyDaemon: block call failed for %s: %s", ticket_id, e)
        self._alert_cc(ticket_id, hold_reason, "audit_fail")
        self._post_channel(
            f"GRANNY_HOLD_AUDIT_FAIL|ticket={ticket_id}|reasons={reasons_str[:120]}"
        )
        self._publish_feed("audit_fail", ticket_id, reasons_str[:200])

    def _hold_for_cc_approval(self, ticket_id: str, reasons: str) -> None:
        """Block ticket and alert CC.0 — HIGH-inertia tickets need human approval."""
        hold_reason = f"HIGH-inertia: needs CC approval — {reasons[:200]}"
        try:
            subprocess.run(
                [_PYTHON, str(_CC_QUEUE), "block", ticket_id, hold_reason],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as e:
            log.warning("GrannyDaemon: block call failed for %s: %s", ticket_id, e)
        self._alert_cc(ticket_id, hold_reason, "high_inertia")
        self._post_channel(
            f"GRANNY_HOLD_HIGH_INERTIA|ticket={ticket_id}|reason={reasons[:120]}"
        )
        self._publish_feed("high_inertia", ticket_id, reasons[:200])
        log.info(
            "GrannyDaemon: %s blocked — HIGH-inertia, CC approval needed", ticket_id
        )

    def _run_orphan_watchdog(self) -> None:
        """Invoke the Scraps orphan watchdog job. Best-effort — never raises."""
        try:
            from devices.scraps.jobs.orphan_watchdog import OrphanWatchdog

            reset = OrphanWatchdog(p90_fn=self._pattern_tracker.p90_minutes).run()
            if reset:
                log.info(
                    "GrannyDaemon: orphan watchdog reset %d ticket(s): %s",
                    len(reset),
                    reset,
                )
        except Exception as e:
            log.warning("GrannyDaemon: orphan watchdog failed: %s", e)

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

    def _publish_feed(self, kind: str, ticket_id: str, details: str) -> None:
        """Publish an event to feeds/granny. Best-effort — never raises."""
        if self._imap is None:
            return
        try:
            env = Envelope.now(
                "Granny.0",
                "feeds/granny",
                {"kind": kind, "ticket_id": ticket_id, "details": details},
            )
            self._imap.append("feeds/granny", env)
        except Exception as e:
            log.warning("GrannyDaemon: feed publish failed (%s): %s", kind, e)

    def _post_channel(self, msg: str) -> None:
        try:
            from unseen_university.channel import post_to_channel

            post_to_channel(
                msg, author="granny-weatherwax", channel="granny-weatherwax"
            )
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
    _GRANNY_HOME.mkdir(parents=True, exist_ok=True)
    _GRANNY_PID_FILE.write_text(str(os.getpid()))
    log.info("GrannyDaemon: wrote PID %d to %s", os.getpid(), _GRANNY_PID_FILE)

    daemon = get_daemon()
    daemon.start()

    def _handle_sig(sig, _frame):
        log.info("GrannyDaemon: received signal %s — shutting down", sig)
        daemon.stop()
        _GRANNY_PID_FILE.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sig)
    signal.signal(signal.SIGINT, _handle_sig)

    # Block main thread
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        daemon.stop()
    finally:
        _GRANNY_PID_FILE.unlink(missing_ok=True)
