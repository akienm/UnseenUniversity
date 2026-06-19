"""GrannyShim — lifecycle shim for the Granny rules-engine daemon."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)

_WATCHDOG_INTERVAL_SEC = int(os.environ.get("GRANNY_SHIM_WATCHDOG_INTERVAL", "30"))
_GRANNY_HOME = Path.home() / ".granny"
_UU_ROOT = Path(__file__).resolve().parents[2]
_TMUX_SESSION = "granny"


def _session_exists() -> bool:
    """Return True if a tmux session named 'granny' is currently active."""
    r = subprocess.run(
        ["tmux", "has-session", "-t", _TMUX_SESSION],
        capture_output=True,
    )
    return r.returncode == 0


class GrannyShim(BaseShim):
    _device_id = "granny-weatherwax"

    def __init__(self) -> None:
        """Watchdog state is in-memory only; _relaunch_count resets on shim restart by design."""
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._daemon = None
        # Diagnostic counter — intentionally in-memory; resets on shim restart.
        self._relaunch_count: int = 0

    @property
    def device_id(self) -> str:
        return self._device_id

    def _get_daemon(self):
        """Return the daemon run_loop callable; caller is responsible for subprocess wrapping."""
        from devices.granny.daemon import run_loop
        return run_loop  # daemon runs as a blocking loop; use subprocess in rack context

    def start(self) -> bool:
        """Start the self-heal watchdog thread. Daemon itself starts via ./granny."""
        if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
            self._watchdog_stop.clear()
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop,
                daemon=True,
                name="granny-watchdog",
            )
            self._watchdog_thread.start()
            log.info("GrannyShim: watchdog started (interval=%ds)", _WATCHDOG_INTERVAL_SEC)
        return True

    def stop(self) -> bool:
        """Signal the watchdog to exit and send SIGTERM to the daemon process."""
        self._watchdog_stop.set()
        pid_file = _GRANNY_HOME / "daemon.pid"
        if pid_file.exists():
            try:
                import signal as _signal
                pid = int(pid_file.read_text().strip())
                os.kill(pid, _signal.SIGTERM)
                log.info("GrannyShim: sent SIGTERM to daemon pid=%d", pid)
            except Exception as e:
                log.warning("GrannyShim: stop failed: %s", e)
        return True

    def restart(self) -> bool:
        """Stop the watchdog + daemon, then re-launch both cleanly."""
        self.stop()
        return self.start()

    def self_test(self) -> dict:
        """Check whether the daemon process is alive by signalling its PID file entry."""
        pid_file = _GRANNY_HOME / "daemon.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)  # signal 0 = existence check
                return {"passed": True, "details": f"daemon running (pid={pid})"}
            except (ProcessLookupError, ValueError):
                return {"passed": False, "details": f"stale pid file — daemon dead (pid={pid_file.read_text().strip()!r})"}
        return {"passed": False, "details": "daemon not running (no pid file)"}

    def rollback(self) -> None:
        """No state to undo — Granny daemon is started externally and is not affected by shim rollback."""
        pass

    def health_surface(self) -> dict:
        """Extend base health with daemon running/stopped status and relaunch count."""
        base = super().health_surface()
        result = {"relaunch_count": str(self._relaunch_count), **base}
        try:
            from devices.granny.daemon import get_daemon
            daemon = get_daemon()
            result["daemon"] = "running" if daemon.is_running() else "stopped"
        except Exception:
            result["daemon"] = "unknown"
        return result

    # ── Self-heal watchdog ────────────────────────────────────────────────────

    def _watchdog_loop(self) -> None:
        """Periodically check for dead daemon + pending sprint tickets → restart."""
        while not self._watchdog_stop.wait(_WATCHDOG_INTERVAL_SEC):
            try:
                self._watchdog_loop_once()
            except Exception as exc:
                log.warning("GrannyShim: watchdog error: %s", exc)

    def _watchdog_loop_once(self) -> None:
        """One watchdog iteration — extracted for testability."""
        result = self.self_test()
        if result["passed"]:
            return
        # Only self-heal when a stale PID file exists (daemon was running before).
        # No PID file = Granny was never started on this host; don't auto-start.
        pid_file = _GRANNY_HOME / "daemon.pid"
        if not pid_file.exists():
            log.debug("GrannyShim: watchdog: no pid file — skipping restart")
            return
        if self._has_pending_tickets():
            log.warning(
                "GrannyShim: watchdog detected stale daemon with pending sprint tickets — restarting"
            )
            self._restart_daemon()
        else:
            log.debug("GrannyShim: watchdog: daemon dead but no pending tickets — skip restart")

    def _restart_daemon(self) -> None:
        """Kill stale tmux session if present, then start a fresh one."""
        try:
            if _session_exists():
                subprocess.run(
                    ["tmux", "kill-session", "-t", _TMUX_SESSION],
                    capture_output=True,
                )
            venv_python = _UU_ROOT / ".venv" / "bin" / "python"
            if not venv_python.exists():
                venv_python = Path(sys.executable)
            subprocess.run(
                ["tmux", "new-session", "-d", "-s", _TMUX_SESSION, "-x", "220", "-y", "50"],
                check=True,
                cwd=_UU_ROOT,
            )
            subprocess.run(
                ["tmux", "send-keys", "-t", _TMUX_SESSION,
                 f"{venv_python} -m devices.granny.daemon", "Enter"],
                check=True,
            )
            self._relaunch_count += 1
            log.info("GrannyShim: daemon restarted (relaunch #%d)", self._relaunch_count)
        except Exception as exc:
            log.error("GrannyShim: _restart_daemon failed: %s", exc)

    def _has_pending_tickets(self) -> bool:
        """Return True when at least one sprint-status ticket exists in the queue.

        Reads the filesystem ticket store (the cutover authority,
        D-build-queue-filesystem-first-2026-06-19), not Postgres.
        """
        try:
            from unseen_university import ticket_store

            return bool(ticket_store.list(status_filter="sprint"))
        except Exception as exc:
            log.debug("GrannyShim: _has_pending_tickets failed: %s", exc)
            return False  # fail-safe: don't restart if we can't confirm there's work
