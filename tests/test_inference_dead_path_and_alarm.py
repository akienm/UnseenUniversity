"""Proof for T-inference-resolve-requests-by-tier (fix #1 + complete-failure alarm).

The dead hardcoded-`openrouter` dispatch path is gone: an unknown model now routes
by task_class through the rules engine to a live source. When NO source is live,
dispatch raises a loud system alarm (`no-provider:<task_class>`) and returns a clean
error instead of falling through to the legacy OPENROUTER_API_KEY-not-set raise.
"""

from __future__ import annotations

import inspect

import pytest

from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.shim import InferenceRequest
from unseen_university.devices.inference.sources import Source, SourceRegistry
from unseen_university import system_alarms


class _FakeSource(Source):
    """Minimal Source: fixed availability + an OpenAI-shaped reply."""

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


def _analyst_models() -> ModelsRegistry:
    # The analyst tier carries a rule deepseek-v4-flash → ollama_cloud (rules_engine).
    return ModelsRegistry(seed=[ModelSpec(
        model_id="deepseek-v4-flash", tier="analyst",
        input_cost_per_1m=0.0, output_cost_per_1m=0.0, context_window=128_000, tags=[],
    )])


@pytest.fixture(autouse=True)
def _redirect_home(tmp_path, monkeypatch):
    monkeypatch.setattr("unseen_university.system_alarms.uu_home", lambda: str(tmp_path))
    monkeypatch.delenv("CC_TMUX_SESSION", raising=False)
    return tmp_path


def test_unknown_model_routes_to_live_source_and_alarms_when_all_down():
    # ── phase 1: unknown model (sanctioned pin) + a live ollama_cloud → live source ──
    # An unknown model resolves to no spec, so it falls through to task_class routing.
    # Under the pin-gate the pin must be sanctioned (pin_reason) — an UNSANCTIONED
    # unknown-model pin is now rejected, not silently rerouted.
    live = SourceRegistry()
    live.register(_FakeSource("ollama_cloud", available=True, text="LIVE-OLLAMA-CLOUD"))
    dev = InferenceDevice(mode="ollama_cloud", endpoint=None, sources=live, models=_analyst_models())
    resp = dev.dispatch(InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="totally-unknown-model-xyz", pin_reason="inference_test",
        task_class="analyst", agent_id="tester",
    ))
    assert resp.finish_reason != "error", f"dead OR path still firing: {resp.text}"
    assert "LIVE-OLLAMA-CLOUD" in resp.text  # routed via rules engine, not hardcoded openrouter

    # ── phase 2: nothing live → complete inference failure → alarm + clean error ──
    dead = SourceRegistry()
    dead.register(_FakeSource("ollama_cloud", available=False))
    dev2 = InferenceDevice(mode="ollama_cloud", endpoint=None, sources=dead, models=_analyst_models())
    resp2 = dev2.dispatch(InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="", task_class="worker", agent_id="igor",
    ))
    assert resp2.finish_reason == "error"
    alarm = system_alarms.get_alarm("no-provider:worker")
    assert alarm is not None, "complete inference failure must raise a system alarm"
    assert alarm["callers"].get("igor") == 1  # caller punch-list names the failing caller


def test_no_dispatch_path_references_hardcoded_openrouter_source():
    """The dead `_sources.get('openrouter')` fallthrough is gone from dispatch."""
    src = inspect.getsource(InferenceDevice.dispatch)
    assert 'get("openrouter")' not in src and "get('openrouter')" not in src
