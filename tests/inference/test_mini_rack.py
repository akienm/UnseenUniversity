"""
Tests for the inference proxy mini-rack: sources, models_registry, rules_engine, health_monitor.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from devices.inference.models_registry import (
    ModelSpec,
    ModelsRegistry,
    default_registry,
)
from devices.inference.sources import (
    OllamaSource,
    OpenRouterSource,
    Source,
    SourceRegistry,
)
from devices.inference.rules_engine import RoutingRule, RulesEngine
from devices.inference.health_monitor import HealthMonitor
from devices.inference.shim import InferenceRequest

# ── ModelsRegistry ─────────────────────────────────────────────────────────────


def test_registry_by_tier_sorted_cheapest_first():
    reg = default_registry()
    workers = reg.by_tier("worker")
    assert workers, "worker tier should have at least one model"
    costs = [m.input_cost_per_1m for m in workers]
    assert costs == sorted(costs)


def test_registry_cheapest_in_tier():
    reg = default_registry()
    cheapest = reg.cheapest_in_tier("minion")
    assert cheapest is not None
    assert cheapest.tier == "minion"


def test_registry_get_by_id():
    reg = default_registry()
    spec = reg.get("qwen/qwen3-coder-30b-a3b-instruct")
    assert spec is not None
    assert spec.tier == "worker"
    assert spec.source_name == "openrouter"


def test_registry_cost_estimate():
    spec = ModelSpec(
        model_id="test/model",
        source_name="openrouter",
        tier="worker",
        input_cost_per_1m=1.0,
        output_cost_per_1m=2.0,
        context_window=8192,
    )
    cost = spec.cost_estimate(input_tokens=1_000_000, output_tokens=500_000)
    assert abs(cost - 2.0) < 0.001  # $1.00 input + $1.00 output


# ── SourceRegistry ─────────────────────────────────────────────────────────────


def test_source_registry_register_and_get():
    reg = SourceRegistry()
    src = MagicMock(spec=Source, name="test-source")
    src.name = "test"
    src.available = True
    reg.register(src)
    assert reg.get("test") is src


def test_source_registry_all_available_filters():
    reg = SourceRegistry()
    s1 = MagicMock(spec=Source)
    s1.name = "up"
    s1.available = True
    s2 = MagicMock(spec=Source)
    s2.name = "down"
    s2.available = False
    reg.register(s1)
    reg.register(s2)
    available = reg.all_available()
    assert len(available) == 1
    assert available[0].name == "up"


# ── RulesEngine ────────────────────────────────────────────────────────────────


def _make_engine(or_available=True, ollama_available=False):
    sources = SourceRegistry()
    or_src = MagicMock(spec=Source)
    or_src.name = "openrouter"
    or_src.available = or_available
    sources.register(or_src)

    ollama_src = MagicMock(spec=Source)
    ollama_src.name = "ollama"
    ollama_src.available = ollama_available
    sources.register(ollama_src)

    models = default_registry()
    return RulesEngine(sources, models), or_src, ollama_src


def test_rules_worker_routes_to_openrouter():
    engine, or_src, _ = _make_engine(or_available=True)
    decision = engine.route("worker")
    assert decision is not None
    assert decision.source is or_src
    assert "qwen3-coder" in decision.model.model_id


def test_rules_minion_routes_to_cheapest():
    engine, or_src, _ = _make_engine(or_available=True)
    decision = engine.route("minion")
    assert decision is not None
    assert decision.model.tier == "minion"


def test_rules_unavailable_source_skipped():
    engine, _, _ = _make_engine(or_available=False, ollama_available=False)
    decision = engine.route("worker")
    assert decision is None


def test_rules_session_affinity_reuses_model():
    engine, or_src, _ = _make_engine(or_available=True)
    d1 = engine.route("worker", session_id="sess-1")
    d2 = engine.route("worker", session_id="sess-1")
    assert d1 is not None and d2 is not None
    assert d1.model.model_id == d2.model.model_id
    assert d2.session_affinity


def test_rules_designer_routes_to_openrouter():
    sources = SourceRegistry()
    or_src = MagicMock(spec=Source)
    or_src.name = "openrouter"
    or_src.available = True
    sources.register(or_src)

    models = default_registry()
    engine = RulesEngine(sources, models)
    decision = engine.route("designer")
    assert decision is not None
    assert decision.source is or_src
    assert "gemini" in decision.model.model_id


def test_rules_worker_prefers_ollama_cloud_over_or():
    """Worker routes to ollama_cloud (flat_rate) over openrouter (usage_based) when both available."""
    sources = SourceRegistry()
    or_src = MagicMock(spec=Source)
    or_src.name = "openrouter"
    or_src.available = True
    or_src.billing_type = "usage_based"
    sources.register(or_src)

    cloud_src = MagicMock(spec=Source)
    cloud_src.name = "ollama_cloud"
    cloud_src.available = True
    cloud_src.billing_type = "flat_rate"
    sources.register(cloud_src)

    models = default_registry()
    engine = RulesEngine(sources, models)
    decision = engine.route("worker")
    assert decision is not None
    assert decision.source is cloud_src, "flat_rate ollama_cloud must be preferred over usage_based OR"


def test_rules_batch_routes_to_local_ollama_at_night():
    """Batch task class uses local_ollama during night hours (02:00)."""
    sources = SourceRegistry()
    local_src = MagicMock(spec=Source)
    local_src.name = "local_ollama"
    local_src.available = True
    local_src.billing_type = "free"
    sources.register(local_src)

    or_src = MagicMock(spec=Source)
    or_src.name = "openrouter"
    or_src.available = True
    or_src.billing_type = "usage_based"
    sources.register(or_src)

    models = default_registry()
    engine = RulesEngine(sources, models)
    decision = engine.route("batch", hour=2)  # 02:00 — night window
    assert decision is not None
    assert decision.source is local_src, "local_ollama must be used for batch at 02:00"


def test_rules_batch_skips_local_ollama_during_day():
    """Batch task class skips local_ollama outside the 00:00-06:00 window."""
    sources = SourceRegistry()
    local_src = MagicMock(spec=Source)
    local_src.name = "local_ollama"
    local_src.available = True
    local_src.billing_type = "free"
    sources.register(local_src)

    cloud_src = MagicMock(spec=Source)
    cloud_src.name = "ollama_cloud"
    cloud_src.available = True
    cloud_src.billing_type = "flat_rate"
    sources.register(cloud_src)

    models = default_registry()
    engine = RulesEngine(sources, models)
    decision = engine.route("batch", hour=14)  # 14:00 — daytime
    assert decision is not None
    assert decision.source is not local_src, "local_ollama must be skipped for batch at 14:00"
    assert decision.source is cloud_src


# ── HealthMonitor ──────────────────────────────────────────────────────────────


def test_health_monitor_check_now_updates_availability():
    sources = SourceRegistry()
    src = MagicMock(spec=Source)
    src.name = "or"
    src.available = True
    src.check_and_update.return_value = False
    sources.register(src)

    monitor = HealthMonitor(sources, interval_sec=3600)
    results = monitor.check_now()
    assert results["or"] is False
    assert src.available is False


# ── InferenceRequest task_class field ─────────────────────────────────────────


def test_inference_request_default_task_class():
    req = InferenceRequest(messages=[{"role": "user", "content": "hi"}])
    assert req.task_class == "worker"


def test_inference_request_task_class_settable():
    req = InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        task_class="minion",
    )
    assert req.task_class == "minion"


# ── Prompt caching ─────────────────────────────────────────────────────────────


def test_cacheable_model_wraps_system_as_content_array():
    """For cacheable models, system message uses content-array + cache_control."""
    src = OpenRouterSource()
    captured = {}

    def _fake_urlopen(req, timeout=None):
        import json

        body = json.loads(req.data)
        captured["messages"] = body["messages"]
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "cache_read_input_tokens": 80,
                },
            }
        ).encode()
        return resp

    req = InferenceRequest(
        messages=[{"role": "user", "content": "hello"}],
        system="You are a helpful coder.",
        model="deepseek/deepseek-v4-flash",
    )
    with (
        patch.object(src, "_api_key", return_value="test-key"),
        patch("urllib.request.urlopen", side_effect=_fake_urlopen),
    ):
        src.call(req)

    sys_msg = captured["messages"][0]
    assert sys_msg["role"] == "system"
    assert isinstance(
        sys_msg["content"], list
    ), "cacheable model should use content array"
    assert sys_msg["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sys_msg["content"][0]["text"] == "You are a helpful coder."


def test_non_cacheable_model_uses_string_system():
    """Non-cacheable models get a plain string system message."""
    src = OpenRouterSource()
    captured = {}

    def _fake_urlopen(req, timeout=None):
        import json

        body = json.loads(req.data)
        captured["messages"] = body["messages"]
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            }
        ).encode()
        return resp

    req = InferenceRequest(
        messages=[{"role": "user", "content": "hello"}],
        system="You are a helpful coder.",
        model="google/gemini-2.0-flash",  # no cacheable tag
    )
    with (
        patch.object(src, "_api_key", return_value="test-key"),
        patch("urllib.request.urlopen", side_effect=_fake_urlopen),
    ):
        src.call(req)

    sys_msg = captured["messages"][0]
    assert sys_msg["role"] == "system"
    assert isinstance(
        sys_msg["content"], str
    ), "non-cacheable model should use plain string"


def test_cache_read_tokens_logged_at_debug(caplog):
    """cache_read_input_tokens > 0 produces a DEBUG log line."""
    import logging

    src = OpenRouterSource()

    def _fake_urlopen(req, timeout=None):
        import json

        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "cache_read_input_tokens": 80,
                },
            }
        ).encode()
        return resp

    req = InferenceRequest(
        messages=[{"role": "user", "content": "hello"}],
        model="deepseek/deepseek-v4-flash",
    )
    with (
        patch.object(src, "_api_key", return_value="test-key"),
        patch("urllib.request.urlopen", side_effect=_fake_urlopen),
        caplog.at_level(logging.DEBUG, logger="devices.inference.sources"),
    ):
        src.call(req)

    assert any("cache hit" in r.message.lower() for r in caplog.records)


def test_cacheable_property_on_modelspec():
    reg = default_registry()
    assert reg.get("deepseek/deepseek-v4-flash").cacheable is True
    assert reg.get("google/gemini-2.0-flash").cacheable is False
