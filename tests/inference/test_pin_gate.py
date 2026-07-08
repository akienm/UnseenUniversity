"""
Tests for T-inference-pin-gate-enforce: the explicit-model path requires a sanctioned
pin_reason. An unsanctioned pin is REJECTED loudly (raise + system_alarm), never
silently rerouted (CP6 — no escape hatches). Unpinned requests route by domain.
"""

from __future__ import annotations

import pytest

from unseen_university import system_alarms
from unseen_university.devices.inference.connections import Connection, ConnectionsRegistry
from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.rules_engine import RulesEngine
from unseen_university.devices.inference.shim import InferenceRequest
from unseen_university.devices.inference.sources import Source, SourceRegistry


class _FakeSource(Source):
    def __init__(self, name: str, available: bool = True, text: str = "ok") -> None:
        self.name = name
        self.available = available
        self.billing_type = "flat_rate"
        self._text = text

    def ping(self) -> bool:
        return self.available

    def call(self, req) -> dict:
        return {
            "choices": [{"message": {"content": self._text}, "finish_reason": "stop"}],
            "model": self.name,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }


def _device_with(model_id: str, source_name: str) -> InferenceDevice:
    src = SourceRegistry()
    src.register(_FakeSource(source_name, available=True, text="DISPATCHED"))
    models = ModelsRegistry(seed=[ModelSpec(
        model_id=model_id, tier="worker",
        input_cost_per_1m=0.0, output_cost_per_1m=0.0, context_window=8192, tags=[],
    )])
    dev = InferenceDevice(mode="ollama_cloud", endpoint=None, sources=src, models=models)
    # Reachability lives on the connections stack (ModelSpec.source_name is deleted): wire
    # the synthetic model<->provider edge so both the pinned path (connections_for) and the
    # unpinned resolve() path can reach it. Mirrors the device's connections+policies=[] build.
    conns = ConnectionsRegistry()
    conns.register(Connection(model_id, source_name, 0.0))
    dev._rules = RulesEngine(src, models, connections=conns, policies=[])
    return dev


@pytest.fixture(autouse=True)
def _redirect_home(tmp_path, monkeypatch):
    monkeypatch.setattr("unseen_university.system_alarms.uu_home", lambda: str(tmp_path))
    monkeypatch.delenv("CC_TMUX_SESSION", raising=False)
    return tmp_path


def test_sanctioned_pin_dispatches_to_that_model():
    """A pin with a sanctioned pin_reason passes the gate and reaches its source."""
    dev = _device_with("pinned-model", "test_src")
    resp = dev.dispatch(InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="pinned-model", pin_reason="model_competition", agent_id="tester",
    ))
    assert resp.finish_reason != "error"
    assert "DISPATCHED" in resp.text


def test_unsanctioned_pin_is_rejected_and_alarms():
    """A pinned model with NO sanctioned pin_reason raises AND fires a system_alarm."""
    dev = _device_with("pinned-model", "test_src")
    with pytest.raises(ValueError, match="unsanctioned model pin"):
        dev.dispatch(InferenceRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="pinned-model", agent_id="rogue",  # no pin_reason
        ))
    alarm = system_alarms.get_alarm("unsanctioned-model-pin:pinned-model")
    assert alarm is not None, "an unsanctioned pin must raise a system_alarm"
    assert alarm["callers"].get("rogue") == 1


def test_bad_pin_reason_is_rejected():
    """A pin_reason that is not in SANCTIONED_PIN_REASONS is rejected (not a free-text bypass)."""
    dev = _device_with("pinned-model", "test_src")
    with pytest.raises(ValueError, match="unsanctioned model pin"):
        dev.dispatch(InferenceRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="pinned-model", pin_reason="because-i-said-so", agent_id="rogue",
        ))


def test_unpinned_request_routes_by_domain_no_gate():
    """An unpinned (model='') request never hits the gate — it routes normally."""
    dev = _device_with("routed-model", "test_src")
    resp = dev.dispatch(InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="", task_class="worker", domain="", agent_id="normal",
    ))
    assert resp.finish_reason != "error"
    assert "DISPATCHED" in resp.text
