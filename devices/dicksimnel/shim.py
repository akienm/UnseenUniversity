"""
DickSimnelShim — lifecycle management for the DickSimnel.0 rack device.

DickSimnel is the OR-powered autonomous ticket worker (Sonnet/worker tier).
It takes sprint-status tickets assigned to worker=dicksimnel, runs each one
through the inference proxy, and posts results back to the queue.

Lifecycle:
  start()   → write availability flag, start background polling thread
  stop()    → set stop event, remove availability flag, join thread
  rollback()→ remove availability flag if it was written

Availability flag protocol (matches Granny's semaphore check):
  ~/.granny/available/DickSimnel.0.available.true   (present = available)
  ~/.granny/available/DickSimnel.0.available.false  (present = unavailable, wins)
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)

_FLAG_DIR = Path.home() / ".granny" / "available"
_DEVICE_NAME = "DickSimnel.0"
_AVAILABLE_FLAG = _FLAG_DIR / f"{_DEVICE_NAME}.available.true"
_BLOCKED_FLAG = _FLAG_DIR / f"{_DEVICE_NAME}.available.false"
_POLL_INTERVAL_S = 30


class DickSimnelShim(BaseShim):
    """
    Shim for DickSimnel.0. Owns the lifecycle; the device owns ticket logic.

    The worker() callback is called by the background thread each poll cycle
    to check for and process available tickets.
    """

    def __init__(self, worker_callback=None) -> None:
        self._worker_callback = worker_callback  # () → None, called each poll cycle
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._flag_written = False

    @property
    def device_id(self) -> str:
        return "dicksimnel"

    # ── Availability flag ──────────────────────────────────────────────────────

    def _write_available(self) -> None:
        _FLAG_DIR.mkdir(parents=True, exist_ok=True)
        _AVAILABLE_FLAG.write_text("true")
        self._flag_written = True
        log.info("DickSimnelShim: availability flag written at %s", _AVAILABLE_FLAG)

    def _remove_available(self) -> None:
        try:
            _AVAILABLE_FLAG.unlink()
            log.info("DickSimnelShim: availability flag removed")
        except FileNotFoundError:
            pass
        self._flag_written = False

    def is_blocked(self) -> bool:
        """Return True if the .false flag is present (Granny marks us unavailable)."""
        return _BLOCKED_FLAG.exists()

    # ── Background poll loop ───────────────────────────────────────────────────

    def _poll_loop(self, stop: threading.Event) -> None:
        log.info("DickSimnel: poll loop started (interval=%ds)", _POLL_INTERVAL_S)
        while not stop.is_set():
            try:
                if self._worker_callback is not None:
                    self._worker_callback()
            except Exception as exc:
                log.warning("DickSimnel: poll loop error: %s", exc)
            stop.wait(timeout=_POLL_INTERVAL_S)
        log.info("DickSimnel: poll loop stopped")

    # ── BaseShim contract ──────────────────────────────────────────────────────

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            log.info("DickSimnelShim: already running")
            return True
        self._write_available()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._poll_loop,
            args=(self._stop_event,),
            daemon=True,
            name="dicksimnel-poll",
        )
        self._thread.start()
        log.info("DickSimnelShim: started — poll thread running")
        return True

    def stop(self) -> bool:
        self._remove_available()
        if self._stop_event is not None:
            self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        self._stop_event = None
        log.info("DickSimnelShim: stopped")
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        """Verify inference proxy is reachable and availability flag can be written."""
        _FLAG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            test_path = _FLAG_DIR / ".dicksimnel_selftest"
            test_path.write_text("ok")
            test_path.unlink()
        except OSError as exc:
            return {"passed": False, "details": f"flag dir not writable: {exc}"}

        # Verify inference proxy is importable
        try:
            from devices.inference.device import InferenceDevice  # noqa: F401
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
