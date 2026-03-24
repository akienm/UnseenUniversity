"""
DaemonSupervisor — central registry for Igor's daemon threads.

Problem: threads were started and forgotten. When the ollama threadlock
surfaced (2026-03-22), there was no way to inspect what threads were running,
whether they were healthy, or surface that in /audit.

Design (T-daemon-supervisor):
  - register(name, thread, health_fn=None) — called once per thread after .start()
  - status() → list of dicts: name, alive, uptime_s, healthy (None if no health_fn)
  - report_str() → formatted string for /audit and get_daemon_report tool

Scope: registry + observability only. Shutdown is NOT orchestrated here — all
registered threads are daemon=True and die with the main process by design (D009).
"""

import time
import threading
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class _DaemonEntry:
    name: str
    thread: threading.Thread
    started_at: float = field(default_factory=time.monotonic)
    health_fn: Callable[[], bool] | None = None


class DaemonSupervisor:
    """Central registry of daemon threads. Singleton via module-level `supervisor`."""

    def __init__(self):
        self._lock = threading.Lock()
        self._entries: dict[str, _DaemonEntry] = {}

    def register(
        self,
        name: str,
        thread: threading.Thread,
        health_fn: Callable[[], bool] | None = None,
    ) -> None:
        """Register a daemon thread. Call after thread.start()."""
        with self._lock:
            self._entries[name] = _DaemonEntry(
                name=name,
                thread=thread,
                health_fn=health_fn,
            )

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
            rows.append(
                {
                    "name": e.name,
                    "alive": alive,
                    "uptime_s": round(now - e.started_at, 1),
                    "healthy": healthy,
                }
            )
        return rows

    def report_str(self) -> str:
        """Formatted report for /audit and get_daemon_report tool."""
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
            lines.append(
                f"  {r['name']:<32}  {alive_mark:<8}  up={r['uptime_s']}s{health_mark}"
            )
        dead = [r for r in rows if not r["alive"]]
        if dead:
            lines.append(
                f"\n⚠ {len(dead)} dead thread(s): {', '.join(r['name'] for r in dead)}"
            )
        return "\n".join(lines)


# Module-level singleton
supervisor = DaemonSupervisor()
