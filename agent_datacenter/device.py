"""
BaseDevice — the rack contract.

Every component that registers on agent_datacenter must implement this interface.
The rack calls these methods for health rollup, lifecycle management, and routing.

Return shapes are intentionally loose dicts rather than typed dataclasses so that
devices can include extra fields without breaking the rack. The rigid keywords are
documented per method; extra keys are allowed.
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from diagnostic_base.base import DiagnosticBase
from diagnostic_base.perf import Stopwatch

if TYPE_CHECKING:
    from bus.imap_server import IMAPServer

INTERFACE_VERSION = "1.0"


class BaseDevice(DiagnosticBase, ABC):
    """Abstract base for all rack devices."""

    def __init__(self, device_id: str = "", **kwargs):
        if not device_id:
            device_id = (
                getattr(type(self), "DEVICE_ID", None) or type(self).__name__.lower()
            )
        super().__init__(device_id=device_id, **kwargs)

    @abstractmethod
    def who_am_i(self) -> dict:
        """Return device identity. Required keys: device_id (str), name (str), version (str)."""

    @abstractmethod
    def requirements(self) -> dict:
        """Return runtime dependencies. Required key: deps (list[str])."""

    @abstractmethod
    def capabilities(self) -> dict:
        """
        Return what this device can do and what envelope keywords it emits.
        Required keys: can_send (bool), can_receive (bool), emitted_keywords (list[str]).
        """

    @abstractmethod
    def comms(self) -> dict:
        """
        Return comms address and direction flags.
        Required keys: address (str comms:// URI), mode (str: read_only|write_only|read_write),
        supports_push (bool), supports_pull (bool), supports_nudge (bool).
        """

    @abstractmethod
    def interface_version(self) -> str:
        """Return the INTERFACE_VERSION this device was built against."""

    @abstractmethod
    def health(self) -> dict:
        """
        Return current health status. Required keys: status (str: healthy|degraded|unhealthy),
        detail (str), checked_at (str ISO 8601).
        """

    @abstractmethod
    def uptime(self) -> float:
        """Return seconds since this device started."""

    @abstractmethod
    def startup_errors(self) -> list:
        """Return list of error strings from the most recent startup attempt."""

    @abstractmethod
    def logs(self) -> dict:
        """
        Return log paths for this device.
        Required key: paths (dict[str, str] subsystem -> log path).
        """

    @abstractmethod
    def update_info(self) -> dict:
        """
        Return update/version metadata.
        Required keys: current_version (str), update_available (bool).
        """

    @abstractmethod
    def where_and_how(self) -> dict:
        """
        Return deployment location and launch method.
        Required keys: host (str), pid (int), launch_command (str).
        """

    @abstractmethod
    def restart(self) -> None:
        """Trigger a graceful restart of this device."""

    @abstractmethod
    def block(self, reason: str) -> None:
        """Block this device from restarting. Rack will not auto-relaunch."""

    @abstractmethod
    def halt(self) -> None:
        """Halt this device immediately. Rack will not restart unless unblocked."""

    @abstractmethod
    def recovery(self) -> None:
        """Attempt recovery from a degraded/unhealthy state."""

    # ── Concrete helpers (not part of the abstract contract) ─────────────────

    def stopwatch(
        self,
        stopwatch_id: str,
        *,
        comment: str = "",
        log_root: Path | None = None,
    ) -> Stopwatch:
        """Return a Stopwatch bound to this device's identity.

        Usage:
            with self.stopwatch("fetch_messages") as t:
                messages = self._imap.fetch_unseen(mailbox)
        """
        device_id = self.who_am_i().get("device_id", type(self).__name__.lower())
        return Stopwatch(
            stopwatch_id,
            device_id=device_id,
            class_name=type(self).__name__,
            comment=comment,
            log_root=log_root or self._log_root,
        )

    def start_heartbeat(
        self,
        imap_server: "IMAPServer",
        interval_s: float = 30.0,
        *,
        stop: threading.Event | None = None,
    ) -> threading.Thread:
        """Start a background thread that publishes heartbeat envelopes.

        Publishes to comms://heartbeat every interval_s seconds. The thread is
        daemon — it stops automatically when the process exits. Pass a stop
        threading.Event to halt it cleanly.

        Returns the started thread so callers can join it on shutdown.
        """
        _stop = stop or threading.Event()

        def _beat() -> None:
            from bus.envelope import Envelope
            from agent_datacenter.bus.router import Router

            router = Router(imap_server)
            device_id = self.who_am_i().get("device_id", "unknown")
            self.info(f"heartbeat: starting for {device_id} (interval={interval_s}s)")
            while not _stop.is_set():
                try:
                    env = Envelope.now(
                        from_device=device_id,
                        to_device="heartbeat",
                        payload={
                            "device_id": device_id,
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "uptime_s": self.uptime(),
                            "health": self.health().get("status", "unknown"),
                        },
                    )
                    router.send("comms://heartbeat", env)
                except Exception as exc:
                    self.debug(f"heartbeat send failed (non-fatal): {exc}")
                _stop.wait(interval_s)
            self.info(f"heartbeat: stopped for {device_id}")

        t = threading.Thread(
            target=_beat, daemon=True, name=f"heartbeat-{type(self).__name__}"
        )
        t.start()
        return t
