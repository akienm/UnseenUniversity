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
    spec = reg.get("deepseek/deepseek-v4-flash")
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
    assert "deepseek" in decision.model.model_id


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


def test_rules_designer_routes_to_anthropic():
    sources = SourceRegistry()
    or_src = MagicMock(spec=Source)
    or_src.name = "openrouter"
    or_src.available = True
    sources.register(or_src)

    anthropic_src = MagicMock(spec=Source)
    anthropic_src.name = "anthropic"
    anthropic_src.available = True
    sources.register(anthropic_src)

    models = default_registry()
    engine = RulesEngine(sources, models)
    decision = engine.route("designer")
    assert decision is not None
    assert decision.source is anthropic_src


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
