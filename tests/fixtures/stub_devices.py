"""
StubDevice and StubShim — minimal fixtures for contract tests.

These provide the minimum correct return values to pass the contract test suite.
Use them in tests that need a registered device without spinning up real infrastructure.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from unseen_university.device import BaseDevice, INTERFACE_VERSION
from unseen_university.shim import BaseShim

_START = time.time()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StubDevice(BaseDevice):
    def who_am_i(self) -> dict:
        return {
            "device_id": "stub",
            "name": "Stub",
            "version": "0.0.0",
            "purpose": "test",
        }

    def requirements(self) -> dict:
        return {"deps": []}

    def capabilities(self) -> dict:
        return {"can_send": False, "can_receive": False, "emitted_keywords": []}

    def comms(self) -> dict:
        return {
            "address": "comms://stub",
            "mode": "read_write",
            "supports_push": False,
            "supports_pull": False,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        return {"status": "healthy", "detail": "stub", "checked_at": _now()}

    def uptime(self) -> float:
        return time.time() - _START

    def startup_errors(self) -> list:
        return []

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "0.0.0", "update_available": False}

    def where_and_how(self) -> dict:
        import os

        return {"host": "localhost", "pid": os.getpid(), "launch_command": "stub"}

    def restart(self) -> None:
        pass

    def block(self, reason: str) -> None:
        pass

    def halt(self) -> None:
        pass

    def recovery(self) -> None:
        pass


class StubShim(BaseShim):
    @property
    def device_id(self) -> str:
        return "stub"

    def start(self) -> bool:
        return True

    def stop(self) -> bool:
        return True

    def restart(self) -> bool:
        return True

    def self_test(self) -> dict:
        return {"passed": True, "details": "stub"}

    def rollback(self) -> None:
        pass
