"""
DickSimnelShim — lifecycle management for the DickSimnel.0 rack device.

DickSimnel receives work via bus dispatch envelopes on the dicksimnel.0
mailbox. The shim connects to the bus in start() and runs a
DickSimnelWorkerListener that polls the mailbox and works tickets
synchronously on dispatch.

Lifecycle:
  start()    → write availability flag, connect bus, start listener
  stop()     → stop listener, remove availability flag
  rollback() → remove availability flag if written
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from unseen_university.shim import BaseShim

if TYPE_CHECKING:
    from unseen_university.devices.dicksimnel.worker_listener import DickSimnelWorkerListener

log = logging.getLogger(__name__)

_FLAG_DIR = Path.home() / ".granny" / "available"
_DEVICE_NAME = "DickSimnel.0"
_AVAILABLE_FLAG = _FLAG_DIR / f"{_DEVICE_NAME}.available.true"
_BLOCKED_FLAG = _FLAG_DIR / f"{_DEVICE_NAME}.available.false"
_MAX_RECONNECT_ATTEMPTS = 3


class DickSimnelShim(BaseShim):
    """
    Shim for DickSimnel.0. Owns the lifecycle; the device owns ticket logic.
    """

    def __init__(self, device=None) -> None:
        """device — optional InferenceDevice; injected for testability, real path uses default_device()."""
        self._device = device
        self._listener = None
        self._flag_written = False
        self._reconnect_count = 0

    @property
    def device_id(self) -> str:
        return "dicksimnel"

    # ── Availability flag ──────────────────────────────────────────────────────

    def _write_available(self) -> None:
        """Write .true flag so Granny considers DickSimnel available for dispatch."""
        # Front-door-spawned device skips flag management (front-door owns the flag)
        if os.environ.get("UU_FRONTDOOR"):
            log.debug("DickSimnelShim: UU_FRONTDOOR set — skipping flag write")
            return
        _FLAG_DIR.mkdir(parents=True, exist_ok=True)
        _AVAILABLE_FLAG.write_text("true")
        self._flag_written = True
        log.info("DickSimnelShim: availability flag written at %s", _AVAILABLE_FLAG)

    def _remove_available(self) -> None:
        """Remove .true flag; Granny will defer future dispatches until start() is called again."""
        # Front-door-spawned device skips flag management (front-door owns the flag)
        if os.environ.get("UU_FRONTDOOR"):
            log.debug("DickSimnelShim: UU_FRONTDOOR set — skipping flag removal")
            return
        try:
            _AVAILABLE_FLAG.unlink()
            log.info("DickSimnelShim: availability flag removed")
        except FileNotFoundError:
            pass
        self._flag_written = False

    def is_blocked(self) -> bool:
        """Return True if the .false flag is present (Granny marks us unavailable)."""
        return _BLOCKED_FLAG.exists()

    # ── Bus connection ─────────────────────────────────────────────────────────

    def _connect_bus(self):
        """Return a bus connection for the dispatch listener, or None if unavailable."""
        try:
            from unseen_university.devices.bus.connection import make_bus_connection
            return make_bus_connection()
        except Exception as exc:
            log.warning(
                "DickSimnelShim: bus unavailable — listener will not receive dispatches: %s", exc
            )
            return None

    # ── BaseShim contract ──────────────────────────────────────────────────────

    def start(self) -> bool:
        """Write availability flag, connect to bus, and launch the worker listener thread."""
        from unseen_university.devices.dicksimnel.worker_listener import DickSimnelWorkerListener
        self._write_available()
        self._reconnect_count = 0
        bus = self._connect_bus()
        self._listener = DickSimnelWorkerListener(
            bus=bus,
            device=self._device,
            on_bus_failure=self._handle_bus_failure,
        )
        self._listener.start()
        log.info("DickSimnelShim: started")
        return True

    def stop(self) -> bool:
        """Stop the worker listener and remove the availability flag."""
        self._remove_available()
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        log.info("DickSimnelShim: stopped")
        return True

    def restart(self) -> bool:
        """Stop then start — reconnects bus and refreshes the listener thread."""
        return self.stop() and self.start()

    def _handle_bus_failure(self, listener: "DickSimnelWorkerListener") -> None:
        """Called by the listener after _FAILURE_THRESHOLD consecutive receive failures.

        Attempts to rebuild the bus connection in-place so the listener resumes
        without a thread restart. After _MAX_RECONNECT_ATTEMPTS failures the shim
        removes the availability flag and silences the listener — manual restart required.
        """
        self._reconnect_count += 1
        if self._reconnect_count > _MAX_RECONNECT_ATTEMPTS:
            log.error(
                "DickSimnelShim: bus reconnect failed %d times — removing availability flag;"
                " manual shim.restart() required",
                _MAX_RECONNECT_ATTEMPTS,
            )
            self._remove_available()
            listener._bus = None  # silence polling — no more warnings
            return

        log.warning(
            "DickSimnelShim: bus failure — reconnect attempt %d/%d",
            self._reconnect_count, _MAX_RECONNECT_ATTEMPTS,
        )
        new_bus = self._connect_bus()
        if new_bus is not None:
            listener._bus = new_bus
            self._reconnect_count = 0
            log.info("DickSimnelShim: bus reconnected successfully")
        else:
            log.warning(
                "DickSimnelShim: reconnect attempt %d/%d failed — will retry after next"
                " failure burst",
                self._reconnect_count, _MAX_RECONNECT_ATTEMPTS,
            )

    def self_test(self) -> dict:
        """Verify flag dir is writable and InferenceDevice is importable."""
        _FLAG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            test_path = _FLAG_DIR / ".dicksimnel_selftest"
            test_path.write_text("ok")
            test_path.unlink()
        except OSError as exc:
            return {"passed": False, "details": f"flag dir not writable: {exc}"}

        try:
            from unseen_university.devices.inference.device import InferenceDevice  # noqa: F401
        except ImportError as exc:
            return {"passed": False, "details": f"InferenceDevice not importable: {exc}"}

        flag_state = "written" if _AVAILABLE_FLAG.exists() else "not written"
        return {
            "passed": True,
            "details": f"flag dir writable; InferenceDevice importable; flag={flag_state}",
        }

    def rollback(self) -> None:
        if self._flag_written:
            self._remove_available()
        log.info("DickSimnelShim: rollback complete")
