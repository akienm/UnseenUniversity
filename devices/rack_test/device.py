"""
RackTestDevice — instrumented test fixture for rack contract testing.

Two modes:
  SIMULATED (default): fully in-process, no external deps, controllable.
  REAL: wraps a real BaseDevice instance, recording all calls through it.

All method calls are appended to recorded_calls for post-hoc assertion.
Failure injection via inject_failure(method_name, exc) makes the next
invocation of that method raise exc instead of returning normally.

The injectable health_status param lets tests exercise health-rollup
paths (healthy / degraded / unhealthy) without patching anything.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from unseen_university.device import BaseDevice, INTERFACE_VERSION

DEVICE_ID = "rack-test"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RackTestDevice(BaseDevice):
    """
    Instrumented BaseDevice for shim and rack-contract tests.

    recorded_calls: list of {method, args, kwargs, result, timestamp}
    Helpers: assert_called, assert_not_called, reset_calls, inject_failure.
    """

    MODE_SIMULATED = "simulated"
    MODE_REAL = "real"

    def __init__(
        self,
        mode: str = MODE_SIMULATED,
        real_device: BaseDevice | None = None,
        health_status: str = "healthy",
    ) -> None:
        if mode == self.MODE_REAL and real_device is None:
            raise ValueError("MODE_REAL requires a real_device argument")
        self._mode = mode
        self._real = real_device
        self._health_status = health_status
        self.recorded_calls: list[dict[str, Any]] = []
        self._failure_map: dict[str, Exception] = {}
        self._blocked = False
        self._block_reason = ""
        super().__init__(device_id=DEVICE_ID)

    # ── Call recording / failure injection ───────────────────────────────────

    def _record(
        self, method: str, *args: Any, result: Any = None, **kwargs: Any
    ) -> Any:
        self.recorded_calls.append(
            {
                "method": method,
                "args": args,
                "kwargs": kwargs,
                "result": result,
                "timestamp": time.time(),
            }
        )
        return result

    def _check_failure(self, method: str) -> None:
        if method in self._failure_map:
            raise self._failure_map.pop(method)

    def _delegate(self, method: str, *args: Any, **kwargs: Any) -> Any:
        """In REAL mode: call real_device method and record the result."""
        fn = getattr(self._real, method)
        result = fn(*args, **kwargs)
        return self._record(method, *args, result=result, **kwargs)

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

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        self._check_failure("who_am_i")
        if self._mode == self.MODE_REAL:
            return self._delegate("who_am_i")
        result = {
            "device_id": DEVICE_ID,
            "name": "RackTestDevice",
            "version": "0.1.0",
            "purpose": "instrumented test fixture for rack-contract and shim tests",
        }
        return self._record("who_am_i", result=result)

    def requirements(self) -> dict:
        self._check_failure("requirements")
        if self._mode == self.MODE_REAL:
            return self._delegate("requirements")
        result = {"deps": []}
        return self._record("requirements", result=result)

    def capabilities(self) -> dict:
        self._check_failure("capabilities")
        if self._mode == self.MODE_REAL:
            return self._delegate("capabilities")
        result = {
            "can_send": False,
            "can_receive": False,
            "emitted_keywords": [],
            "test_fixture": True,
        }
        return self._record("capabilities", result=result)

    def comms(self) -> dict:
        self._check_failure("comms")
        if self._mode == self.MODE_REAL:
            return self._delegate("comms")
        result = {
            "address": f"comms://{DEVICE_ID}/inbox",
            "mode": "read_write",
            "supports_push": False,
            "supports_pull": False,
            "supports_nudge": False,
        }
        return self._record("comms", result=result)

    def interface_version(self) -> str:
        self._check_failure("interface_version")
        if self._mode == self.MODE_REAL:
            return self._delegate("interface_version")
        return self._record("interface_version", result=INTERFACE_VERSION)

    def health(self) -> dict:
        self._check_failure("health")
        if self._mode == self.MODE_REAL:
            return self._delegate("health")
        result = {
            "status": self._health_status,
            "detail": f"simulated ({self._health_status})",
            "checked_at": _now(),
        }
        return self._record("health", result=result)

    def uptime(self) -> float:
        self._check_failure("uptime")
        if self._mode == self.MODE_REAL:
            return self._delegate("uptime")
        result = self.elapsed_s()
        return self._record("uptime", result=result)

    def startup_errors(self) -> list:
        self._check_failure("startup_errors")
        if self._mode == self.MODE_REAL:
            return self._delegate("startup_errors")
        return self._record("startup_errors", result=[])

    def logs(self) -> dict:
        self._check_failure("logs")
        if self._mode == self.MODE_REAL:
            return self._delegate("logs")
        result = {"paths": {}}
        return self._record("logs", result=result)

    def update_info(self) -> dict:
        self._check_failure("update_info")
        if self._mode == self.MODE_REAL:
            return self._delegate("update_info")
        result = {"current_version": "0.1.0", "update_available": False}
        return self._record("update_info", result=result)

    def where_and_how(self) -> dict:
        self._check_failure("where_and_how")
        if self._mode == self.MODE_REAL:
            return self._delegate("where_and_how")
        result = {
            "host": "localhost",
            "pid": os.getpid(),
            "launch_command": "in-process test fixture",
        }
        return self._record("where_and_how", result=result)

    def restart(self) -> None:
        self._check_failure("restart")
        if self._mode == self.MODE_REAL:
            self._delegate("restart")
            return
        self._blocked = False
        self._block_reason = ""
        self._record("restart")

    def block(self, reason: str) -> None:
        self._check_failure("block")
        if self._mode == self.MODE_REAL:
            self._delegate("block", reason)
            return
        self._blocked = True
        self._block_reason = reason
        self._record("block", reason)

    def halt(self) -> None:
        self._check_failure("halt")
        if self._mode == self.MODE_REAL:
            self._delegate("halt")
            return
        self._blocked = True
        self._block_reason = "halt"
        self._record("halt")

    def recovery(self) -> None:
        self._check_failure("recovery")
        if self._mode == self.MODE_REAL:
            self._delegate("recovery")
            return
        self._blocked = False
        self._block_reason = ""
        self._record("recovery")
