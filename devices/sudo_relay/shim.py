"""
SudoRelayShim — lifecycle shim for the sudo relay rack device.

Default state: OFF. The daemon never starts on rack boot; Akien (guru)
starts it manually via /start in the sudo-relay chat.

Registers with the skeleton flat-file registry on start() so the device
appears in the web device list.
"""

from __future__ import annotations

import logging

from config.device_config import DeviceConfig
from skeleton.registry import DeviceRegistry
from unseen_university.shim import BaseShim

from .device import SudoRelayDevice

log = logging.getLogger(__name__)


class SudoRelayShim(BaseShim):
    DEVICE_ID = "sudo-relay"

    def __init__(self, registry: DeviceRegistry | None = None) -> None:
        self._device = SudoRelayDevice()
        self._registry = registry or DeviceRegistry()

    @property
    def device_id(self) -> str:
        return self.DEVICE_ID

    @property
    def device(self) -> SudoRelayDevice:
        return self._device

    def start(self) -> bool:
        """Register with skeleton. Daemon stays OFF until Akien runs /start."""
        try:
            self._registry.register(
                device_id=self.DEVICE_ID,
                config=DeviceConfig(),
                mailbox=f"comms://{self.DEVICE_ID}",
                name="SudoRelay",
                agent_class="utility",
            )
            log.info("SudoRelayShim: registered (daemon OFF — start via /start in chat)")
            return True
        except Exception as exc:
            log.exception("SudoRelayShim.start() failed: %s", exc)
            return False

    def stop(self) -> bool:
        return True

    def restart(self) -> bool:
        return self.start()

    def self_test(self) -> dict:
        state = self._device.state()
        if state == "OFF":
            return {"passed": False, "details": "daemon OFF (start via /start)"}
        return {"passed": True, "details": f"state={state}"}

    def rollback(self) -> None:
        pass
