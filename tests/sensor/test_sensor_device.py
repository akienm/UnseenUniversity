"""
Unit tests for SensorDevice — in-process system telemetry collection.

SensorDevice uses psutil directly and has no external deps at construction
time, so these tests run without any mocking of infra.
"""

from __future__ import annotations

import time

import pytest

from devices.sensor.device import SensorDevice
from unseen_university.device import INTERFACE_VERSION


@pytest.fixture
def device():
    return SensorDevice()


# ── BaseDevice contract ───────────────────────────────────────────────────────


def test_who_am_i_has_required_keys(device):
    info = device.who_am_i()
    assert info["device_id"] == "sensor"
    assert "name" in info
    assert "version" in info


def test_requirements_has_deps(device):
    reqs = device.requirements()
    assert "deps" in reqs
    assert isinstance(reqs["deps"], list)


def test_capabilities_has_required_keys(device):
    caps = device.capabilities()
    assert "can_send" in caps
    assert "can_receive" in caps
    assert "emitted_keywords" in caps


def test_capabilities_emits_sensor_reading(device):
    caps = device.capabilities()
    assert "sensor_reading" in caps["emitted_keywords"]


def test_comms_has_required_keys(device):
    c = device.comms()
    for key in ("address", "mode", "supports_push", "supports_pull", "supports_nudge"):
        assert key in c, f"comms missing key: {key}"


def test_comms_address_starts_with_comms(device):
    assert device.comms()["address"].startswith("comms://")


def test_interface_version(device):
    assert device.interface_version() == INTERFACE_VERSION


def test_health_returns_valid_structure(device):
    h = device.health()
    assert h["status"] in ("healthy", "degraded", "unhealthy")
    assert "detail" in h
    assert "checked_at" in h


def test_health_is_healthy_when_psutil_available(device):
    h = device.health()
    # psutil is installed — sensor should report healthy
    assert h["status"] == "healthy"


def test_uptime_is_positive(device):
    time.sleep(0.01)
    assert device.uptime() > 0


def test_startup_errors_is_list(device):
    assert isinstance(device.startup_errors(), list)


def test_startup_errors_empty_when_psutil_present(device):
    assert device.startup_errors() == []


def test_logs_returns_paths_key(device):
    logs = device.logs()
    assert "paths" in logs


def test_update_info_has_required_keys(device):
    info = device.update_info()
    assert "current_version" in info
    assert "update_available" in info


def test_where_and_how_has_required_keys(device):
    w = device.where_and_how()
    for key in ("host", "pid", "launch_command"):
        assert key in w, f"where_and_how missing key: {key}"


def test_where_and_how_pid_is_positive(device):
    import os

    assert device.where_and_how()["pid"] == os.getpid()


def test_restart_does_not_raise(device):
    device.restart()  # no-op — should not raise


def test_halt_does_not_raise(device):
    device.halt()  # no-op — should not raise


def test_block_adds_to_startup_errors(device):
    device.block("test reason")
    assert any("blocked" in e for e in device.startup_errors())


def test_recovery_clears_startup_errors(device):
    device.block("reason")
    assert device.startup_errors()
    device.recovery()
    assert device.startup_errors() == []


def test_block_makes_health_unhealthy(device):
    device.block("external block")
    h = device.health()
    assert h["status"] == "unhealthy"


def test_recovery_restores_health(device):
    device.block("external block")
    device.recovery()
    h = device.health()
    assert h["status"] == "healthy"


# ── read() output structure ───────────────────────────────────────────────────


def test_read_returns_all_top_level_keys(device):
    result = device.read()
    for key in (
        "sampled_at",
        "cpu",
        "memory",
        "swap",
        "disk",
        "cameras",
        "audio_inputs",
    ):
        assert key in result, f"read() missing key: {key}"


def test_read_cpu_structure(device):
    cpu = device.read()["cpu"]
    assert "overall_percent" in cpu
    assert "per_core" in cpu
    assert "core_count" in cpu
    assert isinstance(cpu["per_core"], list)
    assert cpu["core_count"] == len(cpu["per_core"])


def test_read_memory_structure(device):
    mem = device.read()["memory"]
    assert "percent_used" in mem
    assert "available_mb" in mem
    assert "total_mb" in mem
    assert mem["total_mb"] > 0


def test_read_swap_structure(device):
    sw = device.read()["swap"]
    assert "percent_used" in sw
    assert "used_mb" in sw
    assert "total_mb" in sw


def test_read_disk_is_list(device):
    disk = device.read()["disk"]
    assert isinstance(disk, list)
    # At least the root partition
    assert len(disk) >= 1
    entry = disk[0]
    for key in ("mountpoint", "percent_used", "free_gb", "total_gb"):
        assert key in entry


def test_read_sampled_at_is_iso8601(device):
    import re

    ts = device.read()["sampled_at"]
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts)


def test_read_cameras_is_list(device):
    assert isinstance(device.read()["cameras"], list)


def test_read_audio_inputs_is_list(device):
    assert isinstance(device.read()["audio_inputs"], list)
