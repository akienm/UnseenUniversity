"""
uc_watchdog.py — T-uc-server-watchdog

Keep utility_closet_server alive. Polls /api/health on
http://localhost:8080. If the server is down for two consecutive checks,
relaunch it via setsid (mirrors the igor launcher pattern). Cooldown
prevents restart storms when the relaunch itself fails.

Akien framing: "Igor should be constantly nudging UC back to awakeness."
This source is the nudge.

Inertia: LOW — additive push source. Touches no existing code paths.
Disable with IGOR_UC_WATCHDOG=false.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from ..igor_base import IgorBase
from .forensic_logger import log_error

if TYPE_CHECKING:
    from ..memory.cortex import Cortex


UC_HOST = "127.0.0.1"
UC_PORT = 8080
DOWN_THRESHOLD = 2  # consecutive down checks before relaunching
RESTART_COOLDOWN_SEC = 120
HEALTH_TIMEOUT_SEC = 2.0


def _uc_server_path() -> Path:
    """Resolve the utility_closet_server.py path (matches igor launcher)."""
    return Path.home() / "TheIgors" / "lab" / "claudecode" / "utility_closet_server.py"


def _venv_python() -> Path:
    return Path.home() / "TheIgors" / "venv" / "bin" / "python"


def _uc_log_path() -> Path:
    return Path.home() / ".TheIgors" / "logs" / "utility_closet.log"


def is_uc_up(
    host: str = UC_HOST, port: int = UC_PORT, timeout: float = HEALTH_TIMEOUT_SEC
) -> bool:
    """Quick TCP connect check — true if server is accepting connections."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def relaunch_uc(
    server_path: Path | None = None,
    python_path: Path | None = None,
    log_path: Path | None = None,
) -> bool:
    """Relaunch utility_closet_server detached via setsid. Returns True on launch."""
    server = server_path or _uc_server_path()
    python = python_path or _venv_python()
    log = log_path or _uc_log_path()

    if not server.exists():
        log_error(kind="UC_WATCHDOG", detail=f"server file not found: {server}")
        return False
    if not python.exists():
        log_error(kind="UC_WATCHDOG", detail=f"venv python not found: {python}")
        return False

    log.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(log, "ab") as logf:
            subprocess.Popen(
                [str(python), str(server)],
                stdout=logf,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # equivalent to setsid
                close_fds=True,
            )
        return True
    except OSError as exc:
        log_error(kind="UC_WATCHDOG", detail=f"relaunch failed: {exc}")
        return False


class UtilityClosetWatchdog(IgorBase):
    """Detect UC server down; relaunch with cooldown.

    Two-strike rule: a single missed health check is treated as transient
    (TCP races, brief unavailability during request handling). Two
    consecutive misses trip a relaunch attempt.
    """

    name: str = "uc_watchdog"
    TIMING_TIER: str = "slow"

    def __init__(self) -> None:
        super().__init__()
        self._consecutive_down = 0
        self._last_restart_ts: Optional[float] = None

    def push(self, cortex: "Cortex") -> list[int]:
        if os.getenv("IGOR_UC_WATCHDOG", "true").lower() not in ("1", "true", "yes"):
            return []

        if is_uc_up():
            self._consecutive_down = 0
            return []

        self._consecutive_down += 1
        if self._consecutive_down < DOWN_THRESHOLD:
            return []

        # Cooldown: don't hammer if we just attempted a relaunch
        now = time.monotonic()
        if (
            self._last_restart_ts is not None
            and now - self._last_restart_ts < RESTART_COOLDOWN_SEC
        ):
            return []

        self._last_restart_ts = now
        launched = relaunch_uc()

        ids: list[int] = []
        try:
            content = (
                f"UC_WATCHDOG|relaunch|consecutive_down={self._consecutive_down}|"
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
                    "service": "utility_closet_server",
                    "consecutive_down": self._consecutive_down,
                    "launched": launched,
                },
            )
            if twm_id:
                ids.append(twm_id)
        except Exception as exc:
            log_error(kind="UC_WATCHDOG", detail=f"twm_push: {exc}")

        if launched:
            self._consecutive_down = 0  # optimistic; next check verifies
        return ids
