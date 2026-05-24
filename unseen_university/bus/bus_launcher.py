"""
BusLauncher — self-healing relaunch of the unseen_university bus.

When a device tries to send a message and the bus (IMAP server / skeleton)
is unreachable, BusLauncher attempts to restart it via agentctl. If relaunch
fails too many times within the restart window, the bus is auto-blocked and
no further attempts are made until a human manually unblocks it.

Self-healing vs manual restart:
    Self-healing: triggered automatically by a failed send(); retries up to
    max_restart_failures within restart_window_seconds, then auto-blocks.
    Manual restart: operator runs agentctl init; clears the block flag.

This is intentionally conservative: the bus is infrastructure. Repeatedly
relaunching a broken bus can mask root causes (misconfiguration, resource
exhaustion). Auto-block after N failures surfaces the problem.
"""

from __future__ import annotations

import logging
import subprocess
import time

from config.device_config import DeviceConfig

log = logging.getLogger(__name__)

_BUS_BLOCK_FLAG: bool = False
_FAILURE_TIMES: list[float] = []


def is_blocked() -> bool:
    return _BUS_BLOCK_FLAG


def clear_block() -> None:
    global _BUS_BLOCK_FLAG, _FAILURE_TIMES
    _BUS_BLOCK_FLAG = False
    _FAILURE_TIMES.clear()
    log.info("bus block cleared — relaunch allowed again")


class BusBlockedError(Exception):
    """Raised when the bus is manually or auto-blocked and send is attempted."""


class BusUnavailableError(Exception):
    """Raised when relaunch fails and the bus remains unreachable."""


class BusLauncher:
    """
    Attempts to relaunch the bus via `agentctl init` and waits for IMAP to respond.

    Intended for use inside Router.send() — not for direct caller use.
    """

    def __init__(
        self,
        config: DeviceConfig | None = None,
        imap_host: str = "127.0.0.1",
        imap_port: int = 143,
    ) -> None:
        self._config = config or DeviceConfig()
        self._imap_host = imap_host
        self._imap_port = imap_port

    def record_failure(self) -> None:
        global _FAILURE_TIMES
        now = time.monotonic()
        _FAILURE_TIMES.append(now)
        cutoff = now - self._config.restart_window_seconds
        _FAILURE_TIMES = [t for t in _FAILURE_TIMES if t >= cutoff]

    def should_auto_block(self) -> bool:
        now = time.monotonic()
        cutoff = now - self._config.restart_window_seconds
        in_window = [t for t in _FAILURE_TIMES if t >= cutoff]
        return len(in_window) >= self._config.max_restart_failures

    def relaunch(self) -> bool:
        """
        Attempt to relaunch the bus via `agentctl init`.

        Returns True if the bus responds to IMAP within 10s, False otherwise.
        Raises BusBlockedError if the bus is already blocked (no relaunch attempted).
        """
        global _BUS_BLOCK_FLAG

        if _BUS_BLOCK_FLAG:
            raise BusBlockedError(
                "Bus is blocked — operator must run 'agentctl init' and "
                "manually clear the block before self-healing resumes."
            )

        log.warning(
            "Bus unreachable — attempting relaunch via agentctl init "
            "(failure count in window: %d/%d)",
            len(_FAILURE_TIMES),
            self._config.max_restart_failures,
        )

        try:
            subprocess.Popen(
                ["agentctl", "init"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            log.error("agentctl not found — cannot self-heal bus")
            self.record_failure()
            if self.should_auto_block():
                _BUS_BLOCK_FLAG = True
                log.error(
                    "Bus auto-blocked after %d failures — manual intervention required",
                    self._config.max_restart_failures,
                )
            return False

        # Wait up to 10s for IMAP to respond
        import imaplib

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                M = imaplib.IMAP4(self._imap_host, self._imap_port)
                M.logout()
                log.info("Bus relaunch succeeded — IMAP responding")
                return True
            except OSError:
                time.sleep(0.5)

        self.record_failure()
        if self.should_auto_block():
            _BUS_BLOCK_FLAG = True
            log.error(
                "Bus auto-blocked after %d failures in %ds window — "
                "run 'agentctl init' and manually clear block to resume",
                self._config.max_restart_failures,
                self._config.restart_window_seconds,
            )
        return False
