"""
HubertShim — lifecycle shim for Hubert.

Hubert is a lightweight device (no subprocess, no daemon). start() registers
him in the skeleton registry; stop() marks him offline.
"""

from __future__ import annotations

import logging

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)


class HubertShim(BaseShim):
    _device_id = "hubert"

    @property
    def device_id(self) -> str:
        return self._device_id

    def start(self) -> bool:
        log.info("START device=hubert")
        return True

    def stop(self) -> bool:
        log.info("STOP device=hubert")
        return True

    def restart(self) -> bool:
        log.info("RESTART device=hubert")
        return self.stop() and self.start()

    def self_test(self) -> dict:
        try:
            from devices.hubert.device import HubertDevice
            d = HubertDevice()
            h = d.health()
            return {"passed": h["status"] == "healthy", "details": h}
        except Exception as e:
            log.warning("SELF_TEST_FAIL device=hubert error=%s", e)
            return {"passed": False, "details": str(e)}

    def rollback(self) -> None:
        self.stop()
