"""GrannyShim — lifecycle shim for the Granny rules-engine daemon."""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)

_WATCHDOG_INTERVAL_SEC = int(os.environ.get("GRANNY_SHIM_WATCHDOG_INTERVAL", "30"))
_GRANNY_HOME = Path.home() / ".granny"


class GrannyShim(BaseShim):
    _device_id = "granny-weatherwax"

    def __init__(self) -> None:
        self._watchdog_stop = threading.Event()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._daemon = None
        self._relaunch_count: int = 0

    @property
    def device_id(self) -> str:
        return self._device_id

    def _get_daemon(self):
        from devices.granny.daemon import run_loop
        return run_loop  # daemon runs as a blocking loop; use subprocess in rack context

    def start(self) -> bool:
        log.info("GrannyShim: daemon runs as standalone process via ./granny")
        return True

    def stop(self) -> bool:
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
        self.stop()
        return True

    def self_test(self) -> dict:
        pid_file = _GRANNY_HOME / "daemon.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)  # signal 0 = existence check
                return {"passed": True, "details": f"daemon running (pid={pid})"}
            except (ProcessLookupError, ValueError):
                pass
        return {"passed": False, "details": "daemon not running (no pid file or stale)"}

    def rollback(self) -> None:
        pass

    def health_surface(self) -> dict:
        base = super().health_surface()
        result = {"relaunch_count": str(self._relaunch_count), **base}
        try:
            from devices.granny.daemon import get_daemon
            daemon = get_daemon()
            result["daemon"] = "running" if daemon.is_running() else "stopped"
        except Exception:
            result["daemon"] = "unknown"
        return result
