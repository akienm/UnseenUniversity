"""
IgorADCShim — Auto-start the ADC web_server device if down on Igor startup.

Igor is a resident of ADC, not its owner. When Igor starts and ADC is not
running, the web UI silently degrades. This shim auto-starts the web_server
device when Igor can't reach /health.

Implements:
  - start(): ping /health; if down, start via WebServerDevice
  - stop(): delegate to WebServerDevice.stop() only if Igor owns the process
  - restart(): stop() + start()
  - self_test(): GET /health, return {passed: bool, details: str}
  - rollback(): stop() on partial start

Design rules (palace/rules/coding):
  - OOP-first: class inherits from IgorBase
  - docs-live-in-code: this docstring names the start/stop/restart cycle
  - Fire-and-forget logging: never raise, log failures via self.log
"""

import logging
import os
from typing import Optional

from ..igor_base import IgorBase

log = logging.getLogger(__name__)

_UC_HTTP_PORT = int(os.environ.get("IGOR_UC_HTTP_PORT", "8082"))
_HEALTH_URL = f"http://localhost:{_UC_HTTP_PORT}/health"


def _check_health(timeout_s: float = 3.0) -> bool:
    """Return True if ADC /health responds within timeout_s, False otherwise."""
    try:
        from devices.web_server.device import _check_health as _wsd_health

        return bool(_wsd_health())
    except Exception:
        return False


class IgorADCShim(IgorBase):
    """Manages ADC web_server device lifecycle from Igor's perspective."""

    def __init__(self):
        super().__init__()
        self._owns_process = False
        self._device = None

    @property
    def device_id(self) -> str:
        return "adc-web-server"

    def _get_device(self):
        if self._device is None:
            from devices.web_server.device import WebServerDevice

            self._device = WebServerDevice()
        return self._device

    def start(self) -> bool:
        """Start ADC if not running. Returns True on success."""
        try:
            if _check_health(timeout_s=3.0):
                self.log.info("ADC already running at %s", _HEALTH_URL)
                self._owns_process = False
                return True

            self.log.info("ADC not responding — starting via WebServerDevice")
            dev = self._get_device()
            dev.start()
            self._owns_process = True

            if _check_health(timeout_s=3.0):
                self.log.info("ADC web_server started successfully")
                return True

            self.log.error("ADC did not respond to /health after start()")
            return False

        except Exception as e:
            self.log.error("IgorADCShim.start() failed: %s", e)
            return False

    def stop(self) -> bool:
        """Stop ADC if Igor owns the process."""
        try:
            if not self._owns_process or self._device is None:
                self.log.debug("ADC stop: Igor does not own the process, no-op")
                return True
            self._device.stop()
            self._owns_process = False
            return True
        except Exception as e:
            self.log.error("IgorADCShim.stop() failed: %s", e)
            return False

    def restart(self) -> bool:
        """Restart ADC: stop() + start(). Returns True on success."""
        try:
            self.stop()
            return self.start()
        except Exception as e:
            self.log.error("IgorADCShim.restart() failed: %s", e)
            return False

    def self_test(self) -> dict:
        """Verify ADC is responding to /health."""
        try:
            if _check_health(timeout_s=3.0):
                return {"passed": True, "details": f"ADC {_HEALTH_URL} responding"}
            return {"passed": False, "details": f"ADC {_HEALTH_URL} not responding"}
        except Exception as e:
            return {"passed": False, "details": f"health check error: {e}"}

    def rollback(self) -> None:
        """Called when start() returns False. Stop any partially-started device."""
        try:
            if self._owns_process:
                self.stop()
        except Exception as e:
            self.log.error("Rollback failed: %s", e)
        finally:
            self._owns_process = False
