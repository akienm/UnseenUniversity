"""CalibreShim — lifecycle management for CalibreDevice."""

from __future__ import annotations

import logging

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)


class CalibreShim(BaseShim):
    """Lifecycle shim for the Calibre rack device.

    CalibreDevice has no persistent daemon — it's a stateless subprocess wrapper.
    start/stop are no-ops; self_test checks calibredb availability.
    """

    def __init__(self, device_id: str = "calibre.0") -> None:
        self._device_id = device_id

    @property
    def device_id(self) -> str:
        return self._device_id

    def start(self) -> bool:
        log.info("CalibreShim: started (stateless — no daemon)")
        return True

    def stop(self) -> bool:
        log.info("CalibreShim: stopped")
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        from devices.calibre.device import _calibredb_available
        available = _calibredb_available()
        return {
            "passed": available,
            "details": "calibredb available" if available else "calibredb not found",
        }

    def rollback(self) -> None:
        pass
