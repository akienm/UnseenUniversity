"""
Tests for T-inference-cost-learn-verify: every dispatch emits a per-call cost+outcome
record — domain, tier, source, tokens, dollars, call_outcome — grep-locatable by
ticket_id ('learn from it every single time'). A failed call records its outcome too.
"""

from __future__ import annotations

import logging

import pytest

from unseen_university import system_alarms
from unseen_university.devices.inference.connections import Connection, ConnectionsRegistry
from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.rules_engine import RulesEngine
from unseen_university.devices.inference.shim import InferenceRequest
from unseen_university.devices.inference.sources import Source, SourceRegistry


class _FakeSource(Source):
    def __init__(self, name: str, available: bool = True) -> None:
        self.name = name
        self.available = available
        self.billing_type = "flat_rate"

    def ping(self) -> bool:
        return self.available

    def call(self, req) -> dict:
        return {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "model": self.name,
            "usage": {"prompt_tokens": 12, "completion_tokens": 7},
        }


def _device(available: bool = True) -> InferenceDevice:
    src = SourceRegistry()
    src.register(_FakeSource("test_src", available=available))
    models = ModelsRegistry(seed=[ModelSpec(
        model_id="m", tier="worker",
        input_cost_per_1m=1.0, output_cost_per_1m=1.0, context_window=8192, tags=[],
    )])
    dev = InferenceDevice(mode="ollama_cloud", endpoint=None, sources=src, models=models)
    # Reachability moved off ModelSpec.source_name onto the connections stack; the synthetic
    # 'm' is not in the authoritative default table, so wire its connection explicitly
    # (mirrors the device's own connections+policies=[] engine build).
    conns = ConnectionsRegistry()
    conns.register(Connection("m", "test_src", 2.0))
    dev._rules = RulesEngine(src, models, connections=conns, policies=[])
    return dev


@pytest.fixture(autouse=True)
def _redirect_home(tmp_path, monkeypatch):
    monkeypatch.setattr("unseen_university.system_alarms.uu_home", lambda: str(tmp_path))
    monkeypatch.delenv("CC_TMUX_SESSION", raising=False)
    return tmp_path


@pytest.fixture
def cost_log():
    """Deterministic capture of the device logger (sidesteps caplog/loguru intercept)."""
    records: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda r: records.append(r.getMessage())
    lg = logging.getLogger("unseen_university.devices.inference.device")
    prev_level = lg.level
    lg.setLevel(logging.INFO)
    lg.addHandler(handler)
    try:
        yield records
    finally:
        lg.removeHandler(handler)
        lg.setLevel(prev_level)


def _cost_records(records):
    return [m for m in records if "cost_record" in m]


def test_dispatch_emits_cost_record_with_all_fields(cost_log):
    """A successful dispatch logs a cost_record with domain, tier, source, tokens, dollars, outcome."""
    dev = _device()
    dev.dispatch(InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        task_class="worker", domain="coding", ticket_id="T-cost-1",
    ))
    recs = _cost_records(cost_log)
    assert recs, "a dispatch must emit a cost_record"
    msg = recs[-1]
    for needle in ("'ticket_id': 'T-cost-1'", "'domain': 'coding'", "'tier': 'worker'",
                   "'source': 'test_src'", "'input_tokens': 12", "'output_tokens': 7",
                   "'call_outcome': 'ok'"):
        assert needle in msg, f"cost_record missing {needle}: {msg}"
    assert "'dollars':" in msg


def test_cost_record_locatable_by_ticket_id(cost_log):
    """The record carries the ticket_id so one ticket's full inference story is greppable."""
    dev = _device()
    dev.dispatch(InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        task_class="worker", domain="coding", ticket_id="T-find-me",
    ))
    assert any("T-find-me" in m for m in _cost_records(cost_log))


def test_failed_call_records_outcome_error_with_cost(cost_log):
    """A no-source (failed) dispatch records call_outcome=error and a cost (0), keyed by ticket_id."""
    dev = _device(available=False)  # source down → complete inference failure
    resp = dev.dispatch(InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        task_class="worker", domain="coding", ticket_id="T-fail-1", agent_id="tester",
    ))
    # A capable model exists but its only provider is down → typed no_provider (was the
    # undifferentiated 'error'; T-inference-typed-no-path-result). source_kind stays 'none'.
    assert resp.finish_reason == "no_provider"
    assert resp.source_kind == "none"
    recs = _cost_records(cost_log)
    assert recs, "a failed dispatch must still emit a cost_record"
    msg = recs[-1]
    assert "'ticket_id': 'T-fail-1'" in msg
    assert "'call_outcome': 'error'" in msg
    assert "'dollars': 0.0" in msg
