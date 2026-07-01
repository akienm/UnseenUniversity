"""T-proxy-source-kind: InferenceResponse carries a reliable local-vs-cloud signal.

The gateway-reroute work (T-inf-reroute-A) must preserve igor's used_api boolean,
which branches routing telemetry (PROC_ROUTING_LOCAL vs _ESCALATE). source_billing_type
can't carry that — it defaults to "usage_based" and a flat_rate CLOUD source is still
cloud. source_kind is the dedicated, non-defaulting signal: local | cloud | none.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.inference.models_registry import ModelSpec, default_registry
from unseen_university.devices.inference.rules_engine import RulesEngine
from unseen_university.devices.inference.shim import InferenceRequest
from unseen_university.devices.inference.sources import (
    OllamaSource,
    OpenRouterSource,
    Source,
    SourceRegistry,
)


@pytest.fixture
def device():
    dev = InferenceDevice(mode="openrouter", sources=SourceRegistry(), models=default_registry())
    yield dev
    dev._health.stop()


def _mock_source(name: str, is_local: bool):
    src = MagicMock()
    src.name = name
    src.available = True
    src.is_local = is_local  # explicit — a bare MagicMock attr is truthy
    src.billing_type = "flat_rate" if is_local else "usage_based"
    src.call.return_value = {
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        "model": "test/model",
    }
    return src


def _dispatch_with(device, src):
    device._sources.register(src)
    device._rules = RulesEngine(device._sources, device._models)
    return device.dispatch(
        InferenceRequest(messages=[{"role": "user", "content": "hi"}], task_class="worker")
    )


def test_is_local_flag_on_real_sources():
    """The flag lives on the source classes, not on billing_type."""
    assert OllamaSource.is_local is True
    assert OpenRouterSource.is_local is False
    assert Source.is_local is False


def test_local_source_yields_source_kind_local(device):
    # No default model routes to local ollama, so register one and select it
    # explicitly — this exercises a genuinely is_local=True source.
    src = _mock_source("ollama", is_local=True)
    device._sources.register(src)
    device._models.register(
        ModelSpec(model_id="local-test", source_name="ollama", tier="t2",
                  input_cost_per_1m=0.0, output_cost_per_1m=0.0,
                  context_window=8192, tags=[])
    )
    device._rules = RulesEngine(device._sources, device._models)
    resp = device.dispatch(
        InferenceRequest(messages=[{"role": "user", "content": "hi"}],
                         model="local-test", task_class="worker",
                         pin_reason="inference_test")
    )
    assert resp.source_kind == "local"


def test_cloud_source_yields_source_kind_cloud(device):
    # flat_rate billing but NOT local — proves source_kind != billing_type.
    resp = _dispatch_with(device, _mock_source("openrouter", is_local=False))
    # getattr-with-default so the pre-implementation tree (no source_kind field)
    # fails with an AssertionError — authentic red for proof_emitter, not a
    # collateral AttributeError.
    assert getattr(resp, "source_kind", "MISSING") == "cloud"
    assert resp.source_billing_type == "usage_based"


def test_no_source_yields_source_kind_none(device):
    # empty registry -> complete-inference-failure chokepoint -> error response
    resp = device.dispatch(
        InferenceRequest(messages=[{"role": "user", "content": "hi"}], task_class="worker")
    )
    assert resp.finish_reason == "error"
    assert resp.source_kind == "none"
