"""
Unit tests for WebServerDevice — BaseDevice contract, lifecycle, health.

Does NOT start a real server subprocess. health() makes a localhost TCP
check; we mock _check_health() to stay fast.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from unseen_university.devices.web_server.device import WebServerDevice
from unseen_university.device import INTERFACE_VERSION


@pytest.fixture
def device():
    return WebServerDevice()


# ── BaseDevice contract ───────────────────────────────────────────────────────


def test_who_am_i_required_keys(device):
    info = device.who_am_i()
    assert info["device_id"] == "web-server"
    assert "name" in info
    assert "version" in info


def test_requirements_has_deps(device):
    reqs = device.requirements()
    assert "deps" in reqs
    assert "starlette" in reqs["deps"]
    assert "uvicorn" in reqs["deps"]


def test_capabilities_has_required_keys(device):
    caps = device.capabilities()
    for key in ("can_send", "can_receive", "emitted_keywords"):
        assert key in caps


def test_comms_has_required_keys(device):
    c = device.comms()
    for key in ("address", "mode", "supports_push", "supports_pull", "supports_nudge"):
        assert key in c


def test_comms_address_starts_with_comms(device):
    assert device.comms()["address"].startswith("comms://")


def test_interface_version(device):
    assert device.interface_version() == INTERFACE_VERSION


def test_health_unhealthy_when_server_not_running(device):
    with patch("unseen_university.devices.web_server.device._check_health", return_value=None):
        h = device.health()
    assert h["status"] == "unhealthy"
    assert "checked_at" in h


def test_health_healthy_when_server_running(device):
    with patch(
        "unseen_university.devices.web_server.device._check_health",
        return_value={"agents_attached": 0},
    ):
        h = device.health()
    assert h["status"] == "healthy"


def test_health_unhealthy_when_blocked(device):
    device.block("test block")
    with patch(
        "unseen_university.devices.web_server.device._check_health", return_value={"agents_attached": 0}
    ):
        h = device.health()
    assert h["status"] == "unhealthy"
    assert "blocked" in h["detail"]


def test_uptime_positive(device):
    time.sleep(0.01)
    assert device.uptime() > 0


def test_startup_errors_is_list(device):
    assert isinstance(device.startup_errors(), list)


def test_logs_has_paths(device):
    logs = device.logs()
    assert "paths" in logs
    assert "web_server" in logs["paths"]


def test_update_info_has_required_keys(device):
    info = device.update_info()
    assert "current_version" in info
    assert "update_available" in info


def test_where_and_how_has_required_keys(device):
    w = device.where_and_how()
    for key in ("host", "pid", "launch_command"):
        assert key in w


# ── Lifecycle ─────────────────────────────────────────────────────────────────


def test_block_marks_blocked(device):
    device.block("test reason")
    assert device._blocked is True
    assert device._block_reason == "test reason"


def test_halt_marks_blocked(device):
    device.halt()
    assert device._blocked is True


def test_restart_unblocks(device):
    device.block("test")
    device.restart()
    assert device._blocked is False


def test_recovery_unblocks(device):
    device.halt()
    device.recovery()
    assert device._blocked is False
