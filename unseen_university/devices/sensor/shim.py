"""
SensorShim — lifecycle shim for SensorDevice.

SensorDevice is in-process (no subprocess to manage), so start/stop are
no-ops. self_test() calls device.read() and verifies the expected keys are
present.
"""

from __future__ import annotations

from unseen_university.shim import BaseShim


class SensorShim(BaseShim):
    _device_id = "sensor"

    @property
    def device_id(self) -> str:
        return self._device_id

    def start(self) -> bool:
        return True

    def stop(self) -> bool:
        return True

    def restart(self) -> bool:
        return True

    def self_test(self) -> dict:
        try:
            from unseen_university.devices.sensor.device import SensorDevice

            d = SensorDevice()
            reading = d.read()
            required = {"cpu", "memory", "disk", "sampled_at"}
            missing = required - set(reading.keys())
            if missing:
                return {"passed": False, "details": f"missing keys: {missing}"}
            return {"passed": True, "details": "sensor read ok"}
        except Exception as exc:
            return {"passed": False, "details": str(exc)}

    def rollback(self) -> None:
        pass
