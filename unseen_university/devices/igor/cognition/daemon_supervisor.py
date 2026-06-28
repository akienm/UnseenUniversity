"""
DaemonSupervisor — central registry for Igor's daemon threads.

Problem: threads were started and forgotten. When the ollama threadlock
surfaced (2026-03-22), there was no way to inspect what threads were running,
whether they were healthy, or surface that in /day-close-audit.

Design (T-daemon-supervisor):
  - register(name, thread, health_fn=None) — called once per thread after .start()
  - status() → list of dicts: name, alive, uptime_s, healthy (None if no health_fn)
  - report_str() → formatted string for /day-close-audit and get_daemon_report tool
  - start_polling(restart_flag_path, poll_interval, critical_names) — active watchdog

T-daemon-supervisor-polling: polling thread (5s) detects dead critical threads and
writes restart.flag so Igor can recover automatically instead of running silently
broken. Non-critical thread deaths are logged but don't trigger restart.

T-daemon-supervisor-spawn-liveness: is_alive() is the wrong signal for one-shot-
per-tick workers like ne-worker and consolidation-worker — natural exit is the
design, not a failure. The real failure mode is "main loop stopped calling the
spawn hook." Detect it via heartbeat(name): callers bump a last_heartbeat_ts at
the top of the spawn hook regardless of whether the idle-gate decided to spawn.
status() reports stale=True when (now - last_heartbeat_ts) > staleness_threshold_secs.
Report-only — no restart.flag (yesterday's crash loop proved restart-on-false-
positive is worse than no detection).
"""

import logging
import time
import threading
from dataclasses import dataclass, field
from typing import Callable
from ..igor_base import IgorBase
from ..igor_base import get_logger

log = get_logger(__name__)

# Critical threads whose death warrants a restart
_DEFAULT_CRITICAL = frozenset({"ne-worker", "consolidation-worker"})


@dataclass
class _DaemonEntry:
    name: str
    thread: threading.Thread
    started_at: float = field(default_factory=time.monotonic)
    health_fn: Callable[[], bool] | None = None
    one_shot: bool = False  # if True, natural exit is expected — no DAEMON_DEAD alert
    last_heartbeat_ts: float = field(default_factory=time.monotonic)
    staleness_threshold_secs: float | None = None


class DaemonSupervisor(IgorBase):
    """Central registry of daemon threads. Singleton via module-level `supervisor`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: dict[str, _DaemonEntry] = {}
        self._polling_started: bool = False

    def register(
        self,
        name: str,
        thread: threading.Thread,
        health_fn: Callable[[], bool] | None = None,
        one_shot: bool = False,
        staleness_threshold_secs: float | None = None,
    ) -> None:
        """Register a daemon thread. Call after thread.start().

        one_shot=True: thread is expected to exit after completing its task.
        Natural termination is not alarmed — no DAEMON_DEAD warning logged.
        Use for startup tasks (boot-check, warmup, etc.), not persistent workers.

        staleness_threshold_secs: for one-shot-per-tick workers, the max age of
        the last heartbeat before the entry is flagged stale. None = no check.
        Call heartbeat(name) from the spawn hook regardless of gate decisions.
        """
        with self._lock:
            # Preserve last_heartbeat_ts across re-registration — otherwise the
            # per-tick register call here would reset the heartbeat independently
            # of the upstream heartbeat() call, making staleness meaningless.
            prior = self._entries.get(name)
            hb = prior.last_heartbeat_ts if prior is not None else time.monotonic()
            self._entries[name] = _DaemonEntry(
                name=name,
                thread=thread,
                health_fn=health_fn,
                one_shot=one_shot,
                last_heartbeat_ts=hb,
                staleness_threshold_secs=staleness_threshold_secs,
            )

    def heartbeat(self, name: str) -> None:
        """Bump last_heartbeat_ts for an entry. No-op if not registered yet.

        Call from the spawn hook's entry point — before the is_alive() re-entry
        guard and before the idle-gate — so the heartbeat signals "main loop
        is still calling this hook," not "hook decided to spawn."
        """
        with self._lock:
            e = self._entries.get(name)
            if e is not None:
                e.last_heartbeat_ts = time.monotonic()

    def status(self) -> list[dict]:
        """Return current status of all registered threads."""
        now = time.monotonic()
        rows = []
        with self._lock:
            entries = list(self._entries.values())
        for e in entries:
            alive = e.thread.is_alive()
            healthy: bool | None = None
            if e.health_fn is not None:
                try:
                    healthy = bool(e.health_fn())
                except Exception:
                    healthy = False
            since_heartbeat = now - e.last_heartbeat_ts
            stale = False
            if e.one_shot and e.staleness_threshold_secs is not None:
                stale = since_heartbeat > e.staleness_threshold_secs
            rows.append(
                {
                    "name": e.name,
                    "alive": alive,
                    "uptime_s": round(now - e.started_at, 1),
                    "healthy": healthy,
                    "one_shot": e.one_shot,
                    "since_heartbeat_s": round(since_heartbeat, 1),
                    "stale": stale,
                }
            )
        return rows

    def report_str(self) -> str:
        """Formatted report for /day-close-audit and get_daemon_report tool."""
        rows = self.status()
        if not rows:
            return "DAEMON SUPERVISOR — no threads registered."
        lines = [f"DAEMON SUPERVISOR — {len(rows)} threads:\n"]
        for r in rows:
            alive_mark = "✓" if r["alive"] else "✗ DEAD"
            health_mark = ""
            if r["healthy"] is True:
                health_mark = "  health=ok"
            elif r["healthy"] is False:
                health_mark = "  health=FAIL"
            stale_mark = "  STALE" if r.get("stale") else ""
            lines.append(
                f"  {r['name']:<32}  {alive_mark:<8}  up={r['uptime_s']}s{health_mark}{stale_mark}"
            )
        # For one-shot workers, "dead" means "not currently running," which is
        # the expected state between ticks — don't alarm on it. Only report
        # long-running workers that died and genuinely-stale one-shot workers.
        dead = [r for r in rows if not r["alive"] and not r.get("one_shot")]
        if dead:
            lines.append(
                f"\n⚠ {len(dead)} dead thread(s): {', '.join(r['name'] for r in dead)}"
            )
        stale = [r for r in rows if r.get("stale")]
        if stale:
            lines.append(
                f"\n⚠ {len(stale)} stale worker(s) — spawn hook stopped being called: "
                f"{', '.join(r['name'] for r in stale)}"
            )
        return "\n".join(lines)

    def start_polling(
        self,
        restart_flag_path: str | None = None,
        poll_interval: float = 5.0,
        critical_names: frozenset[str] | None = None,
    ) -> None:
        """Start a daemon watchdog thread that polls registered threads every
        `poll_interval` seconds. If a critical thread dies, writes restart.flag
        (triggering Igor restart) and logs DAEMON_DEAD forensically.
        Non-critical deaths are logged at WARNING level only.
        Call once from Igor boot after all threads are registered."""
        if self._polling_started:
            return
        self._polling_started = True
        _critical = critical_names if critical_names is not None else _DEFAULT_CRITICAL
        _restart_path = restart_flag_path

        def _poll_loop():
            _alerted: set[str] = set()  # names already alerted this lifetime
            while True:
                time.sleep(poll_interval)
                try:
                    rows = self.status()
                    for r in rows:
                        if not r["alive"] and r["name"] not in _alerted:
                            _alerted.add(r["name"])
                            # one_shot threads are expected to exit — no alert
                            if r.get("one_shot"):
                                continue
                            if r["name"] in _critical:
                                log.error(
                                    "DAEMON_DEAD critical thread %s died after %.0fs — writing restart.flag",
                                    r["name"],
                                    r["uptime_s"],
                                )
                                try:
                                    from ..cognition.forensic_logger import (
                                        log_error as _fe,
                                    )

                                    _fe(
                                        kind="DAEMON_DEAD",
                                        detail=f"critical thread {r['name']} died after {r['uptime_s']}s",
                                    )
                                except Exception as _exc:
                                    from .forensic_logger import log_error as _le

                                    _le(
                                        kind="SILENT_EXCEPT",
                                        detail=f"daemon_supervisor.py:161: {_exc}",
                                    )
                                if _restart_path:
                                    try:
                                        open(_restart_path, "w").close()
                                    except Exception as _e:
                                        log.error(
                                            "DAEMON_DEAD: could not write restart.flag: %s",
                                            _e,
                                        )
                            else:
                                log.warning(
                                    "DAEMON_DEAD non-critical thread %s died after %.0fs",
                                    r["name"],
                                    r["uptime_s"],
                                )
                except Exception as _e:
                    log.debug("daemon_supervisor poll error: %s", _e)

        _t = threading.Thread(
            target=_poll_loop, daemon=True, name="daemon-supervisor-poll"
        )
        _t.start()


# Module-level singleton
supervisor = DaemonSupervisor()
