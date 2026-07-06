"""
AiderShim — lifecycle for an Aider.N rack builder.

Mirrors DickSimnelShim: start() writes the availability flag, connects the bus,
and launches an AiderWorkerListener that polls the mailbox and builds tickets
synchronously. Diverges in self_test(): aider's health is the aider BINARY being
present and Hex (ollama) being reachable — not an InferenceDevice import — because
the device shells to an external tool rather than calling the inference proxy.

Lifecycle:
  start()    -> write availability flag, connect bus, start listener
  stop()     -> stop listener, remove availability flag
  rollback() -> remove availability flag if written
"""

from __future__ import annotations

import logging
import os
import signal
import socket
from pathlib import Path
from urllib.parse import urlparse
from typing import TYPE_CHECKING

from unseen_university.shim import BaseShim

from .consts import INSTANCE_ABBREVIATION, HEX_OLLAMA, aider_bin

if TYPE_CHECKING:
    from .worker_listener import AiderWorkerListener

log = logging.getLogger(__name__)

_FLAG_DIR = Path.home() / ".granny" / "available"
_MAX_RECONNECT_ATTEMPTS = 3


class AiderShim(BaseShim):
    """Shim for an Aider.N instance. Owns lifecycle; the device owns build logic."""

    def __init__(self, device=None) -> None:
        self._device = device
        # Per-instance name (Aider.0, Aider.1, …) so multiple instances per box get
        # distinct availability flags. Falls back to Aider.0 when no device wired.
        num = getattr(device, "instance_number", 0) if device is not None else 0
        self._device_name = f"{INSTANCE_ABBREVIATION}.{num}"
        self._listener = None
        self._flag_written = False
        self._reconnect_count = 0

    @property
    def device_id(self) -> str:
        return "aider"

    @property
    def _available_flag(self) -> Path:
        return _FLAG_DIR / f"{self._device_name}.available.true"

    @property
    def _blocked_flag(self) -> Path:
        return _FLAG_DIR / f"{self._device_name}.available.false"

    # ── Availability flag ──────────────────────────────────────────────────────

    def _write_available(self) -> None:
        if os.environ.get("UU_FRONTDOOR"):
            log.debug("AiderShim: UU_FRONTDOOR set — skipping flag write")
            return
        _FLAG_DIR.mkdir(parents=True, exist_ok=True)
        self._available_flag.write_text("true")
        self._flag_written = True
        log.info("AiderShim: availability flag written at %s", self._available_flag)

    def _remove_available(self) -> None:
        if os.environ.get("UU_FRONTDOOR"):
            log.debug("AiderShim: UU_FRONTDOOR set — skipping flag removal")
            return
        try:
            self._available_flag.unlink()
            log.info("AiderShim: availability flag removed")
        except FileNotFoundError:
            pass
        self._flag_written = False

    def is_blocked(self) -> bool:
        return self._blocked_flag.exists()

    # ── Bus connection ─────────────────────────────────────────────────────────

    def _connect_bus(self):
        try:
            from unseen_university.devices.bus.connection import make_bus_connection
            return make_bus_connection()
        except Exception as exc:
            log.warning("AiderShim: bus unavailable — listener will not receive dispatches: %s", exc)
            return None

    # ── BaseShim contract ──────────────────────────────────────────────────────

    def start(self) -> bool:
        from .worker_listener import AiderWorkerListener
        self._write_available()
        self._reconnect_count = 0
        bus = self._connect_bus()
        num = getattr(self._device, "instance_number", 0) if self._device is not None else 0
        self._listener = AiderWorkerListener(
            bus=bus,
            device=self._device,
            device_mailbox=f"aider.{num}",
            on_bus_failure=self._handle_bus_failure,
            on_idle_shutdown=self._on_idle_shutdown,
        )
        self._listener.start()
        log.info("AiderShim: started (%s)", self._device_name)
        return True

    def stop(self) -> bool:
        self._remove_available()
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        log.info("AiderShim: stopped")
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def _handle_bus_failure(self, listener: "AiderWorkerListener") -> None:
        self._reconnect_count += 1
        if self._reconnect_count > _MAX_RECONNECT_ATTEMPTS:
            log.error(
                "AiderShim: bus reconnect failed %d times — removing availability flag;"
                " manual shim.restart() required", _MAX_RECONNECT_ATTEMPTS,
            )
            self._remove_available()
            listener._bus = None
            return
        log.warning("AiderShim: bus failure — reconnect attempt %d/%d",
                    self._reconnect_count, _MAX_RECONNECT_ATTEMPTS)
        new_bus = self._connect_bus()
        if new_bus is not None:
            listener._bus = new_bus
            self._reconnect_count = 0
            log.info("AiderShim: bus reconnected successfully")

    def self_test(self) -> dict:
        """Verify flag dir writable, aider binary present, and Hex reachable.

        These are aider's real health surface — the device is only a builder if it
        can invoke aider AND reach an ollama endpoint. Hex reachability is a fast
        TCP connect (2s), never a full inference call.
        """
        _FLAG_DIR.mkdir(parents=True, exist_ok=True)
        try:
            probe = _FLAG_DIR / f".{self._device_name}_selftest"
            probe.write_text("ok")
            probe.unlink()
        except OSError as exc:
            return {"passed": False, "details": f"flag dir not writable: {exc}"}

        bin_path = aider_bin()
        if not bin_path.exists():
            return {"passed": False, "details": f"aider binary not found at {bin_path} "
                                                f"(set AIDER_BIN or install into ~/.aider-venv)"}

        reachable, detail = self._hex_reachable()
        if not reachable:
            return {"passed": False, "details": f"Hex ollama unreachable at {HEX_OLLAMA}: {detail}"}

        flag_state = "written" if self._available_flag.exists() else "not written"
        return {"passed": True,
                "details": f"aider={bin_path.name}; Hex reachable; flag dir writable; flag={flag_state}"}

    @staticmethod
    def _hex_reachable() -> tuple[bool, str]:
        parsed = urlparse(HEX_OLLAMA)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 11434
        try:
            with socket.create_connection((host, port), timeout=2):
                return True, "ok"
        except OSError as exc:
            return False, str(exc)

    def rollback(self) -> None:
        if self._flag_written:
            self._remove_available()
        log.info("AiderShim: rollback complete")

    def _on_idle_shutdown(self) -> None:
        log.info("AiderShim: idle-sleep — terminating process")
        os.kill(os.getpid(), signal.SIGTERM)
