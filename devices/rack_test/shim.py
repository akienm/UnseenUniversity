"""
RackTestShim — lifecycle shim for RackTestDevice.

Records all lifecycle calls so tests can assert on shim behaviour without
a real process to manage. Pairs with RackTestDevice for full rack-contract
coverage in unit tests.
"""

from __future__ import annotations

import time
from typing import Any

from unseen_university.shim import BaseShim

from .device import RackTestDevice


class RackTestShim(BaseShim):
    """
    Instrumented shim for rack-contract and lifecycle tests.

    All calls are recorded in recorded_calls. Failure injection via
    inject_failure(method_name, exc) makes the next call to that method
    raise exc and record a failed attempt.
    """

    def __init__(self, device: RackTestDevice | None = None) -> None:
        self._device = device or RackTestDevice()
        self.recorded_calls: list[dict[str, Any]] = []
        self._failure_map: dict[str, Exception] = {}
        self._started = False

    # ── Call recording / failure injection ───────────────────────────────────

    def _record(self, method: str, result: Any = None) -> Any:
        self.recorded_calls.append(
            {"method": method, "result": result, "timestamp": time.time()}
        )
        return result

    def _check_failure(self, method: str) -> None:
        if method in self._failure_map:
            self._record(method, result="FAILED")
            raise self._failure_map.pop(method)

    def inject_failure(self, method: str, exc: Exception) -> None:
        """Make the next call to method raise exc."""
        self._failure_map[method] = exc

    def reset_calls(self) -> None:
        self.recorded_calls.clear()

    def assert_called(self, method: str) -> None:
        names = [c["method"] for c in self.recorded_calls]
        if method not in names:
            raise AssertionError(f"{method!r} was never called; calls={names}")

    def assert_not_called(self, method: str) -> None:
        names = [c["method"] for c in self.recorded_calls]
        if method in names:
            raise AssertionError(f"{method!r} was called unexpectedly; calls={names}")

    # ── BaseShim contract ─────────────────────────────────────────────────────

    @property
    def device_id(self) -> str:
        return "rack-test"

    def start(self) -> bool:
        self._check_failure("start")
        self._started = True
        return self._record("start", result=True)

    def stop(self) -> bool:
        self._check_failure("stop")
        self._started = False
        return self._record("stop", result=True)

    def restart(self) -> bool:
        self._check_failure("restart")
        self._started = True
        return self._record("restart", result=True)

    def self_test(self) -> dict:
        self._check_failure("self_test")
        result = {"passed": True, "details": "rack-test fixture self-test OK"}
        return self._record("self_test", result=result)

    def rollback(self) -> None:
        self._check_failure("rollback")
        self._started = False
        self._record("rollback")
