"""
Unit tests for InferenceDevice — contract methods, dispatch, lifecycle.

Mocks network calls so tests don't require OR API key or Ollama.
HealthMonitor background thread is stopped in teardown.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.inference.models_registry import default_registry
from unseen_university.devices.inference.shim import InferenceRequest, InferenceResponse
from unseen_university.devices.inference.sources import SourceRegistry
from unseen_university.device import INTERFACE_VERSION


@pytest.fixture
def or_device():
    sources = SourceRegistry()
    dev = InferenceDevice(mode="openrouter", sources=sources, models=default_registry())
    yield dev
    dev._health.stop()


@pytest.fixture
def ollama_device():
    sources = SourceRegistry()
    dev = InferenceDevice(
        mode="ollama",
        endpoint="http://127.0.0.1:11434",
        sources=sources,
        models=default_registry(),
    )
    yield dev
    dev._health.stop()


# ── BaseDevice contract ───────────────────────────────────────────────────────


def test_who_am_i_device_id(or_device):
    assert or_device.who_am_i()["device_id"] == "inference"


def test_who_am_i_includes_mode(or_device):
    info = or_device.who_am_i()
    assert info["mode"] == "openrouter"


def test_requirements_openrouter(or_device):
    reqs = or_device.requirements()
    assert "deps" in reqs
    assert "system" in reqs
    assert any("OPENROUTER_API_KEY" in s for s in reqs["system"])


def test_requirements_ollama(ollama_device):
    reqs = ollama_device.requirements()
    assert any("ollama" in s.lower() for s in reqs.get("system", []))


def test_capabilities_has_dispatch(or_device):
    caps = or_device.capabilities()
    assert "dispatch" in caps.get("public_methods", [])


def test_comms_address(or_device):
    assert or_device.comms()["address"] == "comms://inference/inbox"


def test_interface_version(or_device):
    assert or_device.interface_version() == INTERFACE_VERSION


def test_health_openrouter_reachable(or_device):
    with patch("unseen_university.devices.inference.device._openrouter_reachable", return_value=True):
        h = or_device.health()
    assert h["status"] == "healthy"


def test_health_openrouter_unreachable(or_device):
    with patch("unseen_university.devices.inference.device._openrouter_reachable", return_value=False):
        h = or_device.health()
    assert h["status"] == "unhealthy"


def test_health_blocked_returns_unhealthy(or_device):
    or_device.block("test block")
    h = or_device.health()
    assert h["status"] == "unhealthy"
    assert "blocked" in h["detail"]


def test_health_ollama_reachable(ollama_device):
    with patch("unseen_university.devices.inference.device._ollama_reachable", return_value=True):
        h = ollama_device.health()
    assert h["status"] == "healthy"


def test_health_ollama_unreachable(ollama_device):
    with patch("unseen_university.devices.inference.device._ollama_reachable", return_value=False):
        h = ollama_device.health()
    assert h["status"] == "unhealthy"


def test_uptime_positive(or_device):
    import time

    time.sleep(0.01)
    assert or_device.uptime() > 0


def test_startup_errors_when_no_key(or_device, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    errs = or_device.startup_errors()
    assert any("OPENROUTER_API_KEY" in e for e in errs)


def test_startup_errors_empty_when_key_set(or_device, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    assert or_device.startup_errors() == []


def test_logs_has_paths(or_device):
    assert "paths" in or_device.logs()


def test_update_info_has_required_keys(or_device):
    info = or_device.update_info()
    assert "current_version" in info
    assert "update_available" in info


def test_where_and_how_has_required_keys(or_device):
    w = or_device.where_and_how()
    for key in ("host", "pid", "launch_command"):
        assert key in w


def test_where_and_how_pid_is_current(or_device):
    assert or_device.where_and_how()["pid"] == os.getpid()


# ── Lifecycle ─────────────────────────────────────────────────────────────────


def test_restart_unblocks(or_device):
    or_device.block("reason")
    or_device.restart()
    assert or_device._blocked is False


def test_block_sets_flag(or_device):
    or_device.block("test")
    assert or_device._blocked is True
    assert or_device._block_reason == "test"


def test_halt_blocks(or_device):
    or_device.halt()
    assert or_device._blocked is True


def test_recovery_unblocks(or_device):
    or_device.halt()
    or_device.recovery()
    assert or_device._blocked is False


# ── capability_graph_query ────────────────────────────────────────────────────


def test_capability_graph_query_returns_list_when_no_db(or_device, monkeypatch):
    monkeypatch.delenv("UU_HOME_DB_URL", raising=False)
    result = or_device.capability_graph_query()
    assert result == []


# ── source_health ─────────────────────────────────────────────────────────────


def test_source_health_returns_dict(or_device):
    result = or_device.source_health()
    assert isinstance(result, dict)


# ── dispatch (mocked source) ──────────────────────────────────────────────────


def test_dispatch_via_rules_engine(or_device):
    """dispatch() routes through mini-rack when a source is available."""
    mock_src = MagicMock()
    mock_src.name = "openrouter"
    mock_src.available = True
    mock_src.call.return_value = {
        "choices": [
            {"message": {"content": "hello from mock"}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "model": "test/model",
    }
    or_device._sources.register(mock_src)
    or_device._rules = __import__(
        "unseen_university.devices.inference.rules_engine", fromlist=["RulesEngine"]
    ).RulesEngine(or_device._sources, or_device._models)

    req = InferenceRequest(
        messages=[{"role": "user", "content": "hi"}], task_class="worker"
    )
    resp = or_device.dispatch(req)
    assert isinstance(resp, InferenceResponse)
    assert resp.text == "hello from mock"


def test_dispatch_returns_error_response_on_no_source(or_device):
    """When no source is available, dispatch returns an error InferenceResponse."""
    req = InferenceRequest(
        messages=[{"role": "user", "content": "hi"}], task_class="worker"
    )
    # SourceRegistry is empty — no sources registered
    resp = or_device.dispatch(req)
    assert isinstance(resp, InferenceResponse)
    # Should be an error response, not raise
    assert resp.text or resp.error
