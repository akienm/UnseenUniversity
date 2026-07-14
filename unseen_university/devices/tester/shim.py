"""TesterShim — lifecycle for the tester rackmount.

The shim owns startup (the Ground Loop stays passive). There is no daemon here and no loop: the
tester is demand-driven — a build arrives, it is graded, a verdict goes back. Nothing to poll.

`self_test()` is the interesting one: it does not ask whether the *device* is fine, it asks
whether the SANDBOX still holds, by building one and probing it from inside. A grader whose
isolation has silently stopped working is worse than no grader, because its verdicts still look
like verdicts.
"""

from __future__ import annotations

import logging

from unseen_university.devices.tester.device import TesterDevice
from unseen_university.devices.tester.isolation import get_isolation
from unseen_university.shim import BaseShim

log = logging.getLogger(__name__)


class TesterShim(BaseShim):
    def __init__(self, device: TesterDevice | None = None) -> None:
        self._device = device or TesterDevice()
        self._started = False

    @property
    def device_id(self) -> str:
        return self._device.DEVICE_ID

    @property
    def device(self) -> TesterDevice:
        return self._device

    def start(self) -> bool:
        ok, why = get_isolation(self._device._isolation_name).available()
        if not ok:
            # Refuse to come up pretending to be a grader we cannot be.
            log.error("TesterShim: refusing to start — %s", why)
            self._device.block(why)
            return False
        self._started = True
        log.info("TesterShim: started (isolation=%s)", self._device._isolation_name)
        return True

    def stop(self) -> bool:
        self._started = False
        log.info("TesterShim: stopped")
        return True

    def restart(self) -> bool:
        return self.stop() and self.start()

    def self_test(self) -> dict:
        """Does the sandbox still SEAL? Built and probed for real — not asserted."""
        iso = get_isolation(self._device._isolation_name)
        ok, why = iso.available()
        if not ok:
            return {"passed": False, "details": why}
        seal = iso.check_seal(cwd=".")
        return {"passed": seal.confirmed, "details": seal.detail}

    def rollback(self) -> None:
        self.stop()
