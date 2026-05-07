"""
WebServerShim — lifecycle shim for the web server device.
"""

from __future__ import annotations

from agent_datacenter.shim import BaseShim

from .device import WebServerDevice, _check_health, _PORT


class WebServerShim(BaseShim):
    DEVICE_ID = "web-server"

    def __init__(self) -> None:
        self._device = WebServerDevice()

    @property
    def device_id(self) -> str:
        return self.DEVICE_ID

    @property
    def device(self) -> WebServerDevice:
        return self._device

    def start(self) -> bool:
        try:
            self._device.start()
            return bool(_check_health())
        except Exception:
            return False

    def stop(self) -> bool:
        try:
            self._device.stop()
            return True
        except Exception:
            return False

    def restart(self) -> bool:
        self.stop()
        return self.start()

    def self_test(self) -> dict:
        data = _check_health()
        if data:
            return {"passed": True, "details": f"port {_PORT} healthy"}
        return {"passed": False, "details": f"no response on port {_PORT}"}

    def rollback(self) -> None:
        self._device.stop()
