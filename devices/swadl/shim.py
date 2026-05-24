"""
SwadlShim — lifecycle stub for the SWADL device.

Placeholder until SWADL API stabilizes and the device is fully implemented.
Replace once SwadlDevice is real.
"""

from __future__ import annotations

import logging

from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)


class SwadlShim(BaseShim):
    """Stub shim for SWADL. Replace once SwadlDevice is implemented."""

    @property
    def device_id(self) -> str:
        return "swadl"

    def start(self) -> bool:
        log.info("SwadlShim.start(): stub — not yet integrated")
        return True

    def stop(self) -> bool:
        log.info("SwadlShim.stop(): stub — not yet integrated")
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        return {
            "passed": False,
            "details": "SWADL stub — not yet integrated; full device pending API stabilization",
        }

    def rollback(self) -> None:
        pass
