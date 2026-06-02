"""
Tests for ArchivistDevice and ArchivistShim.

All tests use a MagicMock InferenceDevice to avoid background threads
and network calls. ArchivistProxy and LearningPipeline are real — their
in-memory logic is under test.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from devices.archivist.device import ArchivistDevice
from devices.archivist.shim import ArchivistShim
from devices.inference.shim import InferenceRequest, InferenceResponse
from skeleton.registry import DeviceRegistry
from unseen_university.device import INTERFACE_VERSION


def _mock_inference(status="healthy"):
    inf = MagicMock()
    inf.health.return_value = {"status": status, "detail": "mock"}
    inf.startup_errors.return_value = []
    inf.dispatch.return_value = InferenceResponse(
        text="test response",
        model="test-model",
    )
    return inf


def _make_request():
    return InferenceRequest(
        messages=[{"role": "user", "content": "hello"}],
        agent_id="test-agent",
        session_id="test-session",
    )


@pytest.fixture
def device():
    return ArchivistDevice(inference=_mock_inference())


@pytest.fixture
def shim(tmp_path):
    registry = DeviceRegistry(path=tmp_path / "devices.json")
    return ArchivistShim(inference=_mock_inference(), registry=registry)


# ── BaseDevice contract ───────────────────────────────────────────────────────


def test_who_am_i_device_id(device):
    assert device.who_am_i()["device_id"] == "archivist"


def test_who_am_i_agent_class_specialized(device):
    assert device.who_am_i()["agent_class"] == "specialized"


def test_capabilities_includes_dispatch(device):
    assert "dispatch" in device.capabilities()["public_methods"]


def test_capabilities_agent_class_specialized(device):
    assert device.capabilities()["agent_class"] == "specialized"


def test_comms_address(device):
    assert device.comms()["address"] == "comms://archivist/inbox"


def test_interface_version(device):
    assert device.interface_version() == INTERFACE_VERSION


def test_health_healthy_when_inference_healthy(device):
    assert device.health()["status"] == "healthy"


def test_health_degraded_when_inference_unhealthy():
    dev = ArchivistDevice(inference=_mock_inference(status="unhealthy"))
    assert dev.health()["status"] == "degraded"


def test_startup_errors_delegates_to_inference(device):
    assert device.startup_errors() == []


# ── Proxy intercept behavior ──────────────────────────────────────────────────


def test_dispatch_returns_inference_response(device):
    resp = device.dispatch(_make_request())
    assert isinstance(resp, InferenceResponse)
    assert resp.text == "test response"


def test_dispatch_calls_inference_dispatch(device):
    req = _make_request()
    device.dispatch(req)
    device._inference.dispatch.assert_called_once_with(req)


def test_dispatch_logs_proxy_intercept(device, caplog):
    with caplog.at_level(logging.INFO, logger="devices.archivist.proxy"):
        device.dispatch(_make_request())
    assert any("PROXY_INTERCEPT" in r.message for r in caplog.records)


def test_dispatch_logs_graph_hit_false(device, caplog):
    with caplog.at_level(logging.INFO, logger="devices.archivist.proxy"):
        device.dispatch(_make_request())
    proxy_records = [r for r in caplog.records if "PROXY_INTERCEPT" in r.message]
    assert len(proxy_records) == 1
    assert "graph_hit=false" in proxy_records[0].message


def test_dispatch_enqueues_learning_payload(device):
    assert device.queue_depth() == 0
    device.dispatch(_make_request())
    assert device.queue_depth() == 1


def test_dispatch_multiple_calls_accumulate_payloads(device):
    for _ in range(3):
        device.dispatch(_make_request())
    assert device.queue_depth() == 3


def test_queue_depth_matches_pipeline(device):
    device.dispatch(_make_request())
    assert device.queue_depth() == device._proxy.pipeline.queue_depth()


# ── ArchivistShim ─────────────────────────────────────────────────────────────


def test_shim_device_id(shim):
    assert shim.device_id == "archivist"


def test_shim_start_returns_true(shim):
    assert shim.start() is True


def test_shim_start_creates_device(shim):
    shim.start()
    assert isinstance(shim.device, ArchivistDevice)


def test_shim_registers_with_skeleton(shim):
    shim.start()
    record = shim._registry.get_device("archivist")
    assert record is not None
    assert record["agent_class"] == "specialized"


def test_shim_registers_correct_mailbox(shim):
    shim.start()
    record = shim._registry.get_device("archivist")
    assert record["mailbox"] == "comms://archivist/inbox"


def test_shim_stop_clears_device(shim):
    shim.start()
    shim.stop()
    assert shim.device is None


def test_shim_restart_recreates_device(shim):
    shim.start()
    d1 = shim.device
    shim.restart()
    assert shim.device is not d1
    assert shim.device is not None


def test_shim_self_test_before_start(shim):
    assert shim.self_test()["passed"] is False


def test_shim_self_test_after_start(shim):
    shim.start()
    result = shim.self_test()
    assert result["passed"] is True


def test_shim_rollback_clears_device(shim):
    shim.start()
    shim.rollback()
    assert shim.device is None


def test_shim_rollback_deregisters_from_skeleton(shim):
    shim.start()
    shim.rollback()
    assert shim._registry.get_device("archivist") is None
