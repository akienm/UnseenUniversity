"""
web_server_watchdog.py — Keep the ADC web_server device alive.

Polls /health on the web server. If the server is down for two consecutive
checks, relaunch it via WebServerDevice.start(). Cooldown prevents restart
storms when the relaunch itself fails.

Akien framing: "Igor should be constantly nudging UC back to awakeness."
This source is the nudge.

Inertia: LOW — additive push source. Touches no existing code paths.
Disable with IGOR_WEB_SERVER_WATCHDOG=false (also honours legacy IGOR_UC_WATCHDOG).
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Optional

from ..igor_base import IgorBase
from .forensic_logger import log_error

if TYPE_CHECKING:
    from ..memory.cortex import Cortex


DOWN_THRESHOLD = 2  # consecutive down checks before relaunching
RESTART_COOLDOWN_SEC = 120


def _is_enabled() -> bool:
    for var in ("IGOR_WEB_SERVER_WATCHDOG", "IGOR_UC_WATCHDOG"):
        val = os.getenv(var)
        if val is not None:
            return val.lower() in ("1", "true", "yes")
    return True


def is_web_server_up() -> bool:
    """True if the ADC web server is accepting health checks."""
    try:
        from unseen_university.devices.web_server.device import _check_health

        return bool(_check_health())
    except Exception:
        return False


def relaunch_web_server() -> bool:
    """Start the web server via WebServerDevice.start(). Returns True if healthy after start."""
    try:
        from unseen_university.devices.web_server.device import WebServerDevice

        dev = WebServerDevice()
        dev.start()
        return is_web_server_up()
    except Exception as exc:
        log_error(kind="WEB_SERVER_WATCHDOG", detail=f"relaunch failed: {exc}")
        return False


class WebServerWatchdog(IgorBase):
    """Detect ADC web server down; relaunch with cooldown.

    Two-strike rule: a single missed health check is treated as transient
    (TCP races, brief unavailability during request handling). Two
    consecutive misses trip a relaunch attempt.
    """

    name: str = "web_server_watchdog"
    TIMING_TIER: str = "slow"

    def __init__(self) -> None:
        super().__init__()
        self._consecutive_down = 0
        self._last_restart_ts: Optional[float] = None

    def push(self, cortex: "Cortex") -> list[int]:
        if not _is_enabled():
            return []

        if is_web_server_up():
            self._consecutive_down = 0
            return []

        self._consecutive_down += 1
        if self._consecutive_down < DOWN_THRESHOLD:
            return []

        now = time.monotonic()
        if (
            self._last_restart_ts is not None
            and now - self._last_restart_ts < RESTART_COOLDOWN_SEC
        ):
            return []

        self._last_restart_ts = now
        launched = relaunch_web_server()

        ids: list[int] = []
        try:
            content = (
                f"WEB_SERVER_WATCHDOG|relaunch|consecutive_down={self._consecutive_down}|"
                f"launched={launched}"
            )
            twm_id = cortex.twm_push(
                source=self.name,
                content_csb=content,
                salience=0.5,
                urgency=0.3,
                ttl_seconds=600,
                category="watchdog",
                metadata={
                    "service": "web_server",
                    "consecutive_down": self._consecutive_down,
                    "launched": launched,
                },
            )
            if twm_id:
                ids.append(twm_id)
        except Exception as exc:
            log_error(kind="WEB_SERVER_WATCHDOG", detail=f"twm_push: {exc}")

        if launched:
            self._consecutive_down = 0  # optimistic; next check verifies
        return ids
