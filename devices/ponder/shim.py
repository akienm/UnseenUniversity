"""
PonderShim — lifecycle shim for Ponder Stibbons.

Lightweight device (no subprocess). start() registers in skeleton registry.
"""
from __future__ import annotations

import logging

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)


class PonderShim(BaseShim):
    _device_id = "ponder"

    @property
    def device_id(self) -> str:
        return self._device_id

    def start(self) -> bool:
        log.info("START device=ponder")
        return True

    def stop(self) -> bool:
        log.info("STOP device=ponder")
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        try:
            from devices.ponder.device import PonderDevice
            h = PonderDevice().health()
            return {"passed": h["status"] == "healthy", "details": h}
        except Exception as e:
            log.warning("SELF_TEST_FAIL device=ponder error=%s", e)
            return {"passed": False, "details": str(e)}

    def rollback(self) -> None:
        self.stop()
