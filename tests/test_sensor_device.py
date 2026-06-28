"""
tests/test_sensor_device.py — Unit tests for SensorDevice and SensorShim.

Covers T-adc-sensor-device: reading each sensor category, health contract,
and MCP-ready JSON shape.
"""

from __future__ import annotations

import pytest

from unseen_university.devices.sensor.device import SensorDevice
from unseen_university.devices.sensor.shim import SensorShim


@pytest.fixture
def device():
    return SensorDevice()


@pytest.fixture
def shim():
    return SensorShim()


class TestSensorDeviceReading:
    def test_read_returns_dict(self, device):
        result = device.read()
        assert isinstance(result, dict)

    def test_read_has_required_keys(self, device):
        result = device.read()
        for key in ("sampled_at", "cpu", "memory", "swap", "disk"):
            assert key in result, f"missing key: {key}"

    def test_cpu_shape(self, device):
        cpu = device.read()["cpu"]
        assert "overall_percent" in cpu
        assert "per_core" in cpu
        assert "core_count" in cpu
        assert 0.0 <= cpu["overall_percent"] <= 100.0
        assert cpu["core_count"] >= 1

    def test_memory_shape(self, device):
        mem = device.read()["memory"]
        assert "percent_used" in mem
        assert "available_mb" in mem
        assert "total_mb" in mem
        assert 0.0 <= mem["percent_used"] <= 100.0
        assert mem["total_mb"] > 0

    def test_disk_shape(self, device):
        disk = device.read()["disk"]
        assert isinstance(disk, list)
        assert len(disk) >= 1
        entry = disk[0]
        for key in ("mountpoint", "device", "percent_used", "free_gb", "total_gb"):
            assert key in entry, f"disk entry missing: {key}"

    def test_temperatures_is_dict(self, device):
        temps = device.read()["temperatures"]
        assert isinstance(temps, dict)

    def test_fans_is_dict(self, device):
        fans = device.read()["fans"]
        assert isinstance(fans, dict)

    def test_cameras_is_list(self, device):
        cameras = device.read()["cameras"]
        assert isinstance(cameras, list)

    def test_audio_inputs_is_list(self, device):
        audio = device.read()["audio_inputs"]
        assert isinstance(audio, list)

    def test_result_is_json_serializable(self, device):
        import json

        result = device.read()
        serialized = json.dumps(result)
        assert isinstance(serialized, str)
        assert len(serialized) > 10


class TestSensorDeviceHealth:
    def test_health_shape(self, device):
        h = device.health()
        assert "status" in h
        assert h["status"] in ("healthy", "degraded", "unhealthy")
        assert "checked_at" in h

    def test_health_is_healthy(self, device):
        h = device.health()
        assert h["status"] == "healthy"


class TestSensorShim:
    def test_device_id(self, shim):
        assert shim.device_id == "sensor"

    def test_start_stop(self, shim):
        assert shim.start() is True
        assert shim.stop() is True

    def test_self_test_passes(self, shim):
        result = shim.self_test()
        assert result["passed"] is True
