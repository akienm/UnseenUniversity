"""
Queue device tests for the tests/ discovery path.

The canonical test suite lives in devices/queue/tests/test_queue_device.py
and covers the full unit + integration surface. This file provides an
additional layer of coverage for the QueueDevice BaseDevice contract methods
that the primary suite doesn't cover, ensuring pytest tests/ includes them.
"""

from __future__ import annotations

import json
import os

import pytest

from unseen_university.devices.queue.device import QueueDevice, LegacyDirectClaimError, _gate_tripped
from unseen_university.device import INTERFACE_VERSION

_DB_URL = os.environ.get("UU_HOME_DB_URL", "")
_skip_no_db = pytest.mark.skipif(not _DB_URL, reason="UU_HOME_DB_URL not set")


@pytest.fixture
def device(tmp_path, monkeypatch):
    # Filesystem ticket store (D-build-queue-filesystem-first) pointed at a tmp dir.
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"tripped": False}))
    import unseen_university.devices.queue.device as dev_mod

    original = dev_mod.GATE_FILE
    dev_mod.GATE_FILE = gate
    dev = QueueDevice()
    dev_mod.GATE_FILE = original
    return dev


# ── BaseDevice contract ───────────────────────────────────────────────────────


def test_who_am_i_required_keys(device):
    info = device.who_am_i()
    assert info["device_id"] == "queue"
    assert "name" in info
    assert "version" in info


def test_requirements_lists_ticket_store(device):
    reqs = device.requirements()
    assert "unseen_university.ticket_store" in reqs.get("deps", [])


def test_capabilities_has_mcp_tools(device):
    caps = device.capabilities()
    for tool in ("queue_next", "queue_peek", "queue_list", "queue_show"):
        assert tool in caps.get("mcp_tools", [])


def test_comms_has_required_keys(device):
    c = device.comms()
    for key in ("address", "mode", "supports_push", "supports_pull", "supports_nudge"):
        assert key in c


def test_comms_address_starts_with_comms(device):
    assert device.comms()["address"].startswith("comms://")


def test_interface_version(device):
    assert device.interface_version() == INTERFACE_VERSION


def test_startup_errors_is_list(device):
    assert isinstance(device.startup_errors(), list)


def test_logs_has_paths_key(device):
    assert "paths" in device.logs()


def test_update_info_has_required_keys(device):
    info = device.update_info()
    assert "current_version" in info
    assert "update_available" in info


def test_where_and_how_has_required_keys(device):
    w = device.where_and_how()
    for key in ("host", "pid", "launch_command"):
        assert key in w


def test_restart_does_not_raise(device):
    device.restart()


def test_block_does_not_raise(device):
    device.block("test reason")


def test_halt_does_not_raise(device):
    device.halt()


def test_recovery_does_not_raise(device):
    device.recovery()


def test_uptime_positive(device):
    import time

    time.sleep(0.01)
    assert device.uptime() > 0


# ── LegacyDirectClaimError ────────────────────────────────────────────────────


def test_queue_claim_raises_legacy_error():
    with pytest.raises(LegacyDirectClaimError):
        QueueDevice.queue_claim()


# ── Gate file ─────────────────────────────────────────────────────────────────


def test_gate_tripped_false_when_no_file(tmp_path):
    import unseen_university.devices.queue.device as dev_mod

    orig = dev_mod.GATE_FILE
    dev_mod.GATE_FILE = tmp_path / "nonexistent.json"
    try:
        assert _gate_tripped() is False
    finally:
        dev_mod.GATE_FILE = orig


def test_gate_tripped_true_when_set(tmp_path):
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"tripped": True}))
    import unseen_university.devices.queue.device as dev_mod

    orig = dev_mod.GATE_FILE
    dev_mod.GATE_FILE = gate
    try:
        assert _gate_tripped() is True
    finally:
        dev_mod.GATE_FILE = orig


# ── queue_next gated ─────────────────────────────────────────────────────────


def test_queue_next_returns_none_when_gate_tripped(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps({"tripped": True}))
    import unseen_university.devices.queue.device as dev_mod

    orig = dev_mod.GATE_FILE
    dev_mod.GATE_FILE = gate
    try:
        dev = QueueDevice()
        assert dev.queue_next("claude") is None
    finally:
        dev_mod.GATE_FILE = orig


# ── Integration (live filesystem ticket store) ─────────────────────────────────


@_skip_no_db
class TestQueueDeviceIntegration:
    @pytest.fixture
    def dev(self):
        return QueueDevice()

    def test_health_returns_healthy(self, dev):
        h = dev.health()
        assert h["status"] == "healthy"

    def test_queue_list_returns_list(self, dev):
        assert isinstance(dev.queue_list(status="sprint"), list)

    def test_queue_show_unknown_returns_none(self, dev):
        assert dev.queue_show("T-no-such-ticket-xyz") is None

    def test_queue_peek_returns_dict_or_none(self, dev):
        result = dev.queue_peek(worker="claude")
        assert result is None or isinstance(result, dict)
