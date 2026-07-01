"""
Tests for T-ds-local-ollama-route (increment 3 of D-inference-cost-optimizing-router):
register Hex's roster as owned-local ModelSpecs on source 'ollama' so the cost-optimizing
selector routes worker/minion/analyst tiers to the free local box, and DS drops its explicit
model_override so it routes by the worker TIER (tier-not-model contract).

Also pins the analyst→code difficulty correction: analyst is reasoning, not architecture, so
a mid-size local reasoner (deepseek-r1:14b) can serve it without overclaiming 'design'.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from unseen_university.devices.inference.models_registry import default_registry
from unseen_university.devices.inference.routing_buckets import task_class_to_difficulty
from unseen_university.devices.inference.rules_engine import RulesEngine, _DEFAULT_RULES
from unseen_university.devices.inference.sources import Source, SourceRegistry

HEX_MODELS = ("llama3.2:3b", "qwen2.5-coder:14b", "deepseek-r1:14b")


def _src(name, cost_class, *, available=True):
    s = MagicMock(spec=Source)
    s.name = name
    s.available = available
    s.cost_class = cost_class
    s.time_bucket = "interactive"
    s.billing_type = "usage_based"
    return s


def _hex_sources(ollama_available=True):
    reg = SourceRegistry()
    reg.register(_src("ollama", "owned_local", available=ollama_available))
    reg.register(_src("google_free", "free_throttled"))
    reg.register(_src("ollama_cloud", "subscription"))
    return reg


# ── Hex models registered on the local ollama source ──────────────────────────


def test_hex_models_registered_on_ollama_source():
    reg = default_registry()
    for mid in HEX_MODELS:
        spec = reg.get(mid)
        assert spec is not None, f"{mid} not registered"
        assert spec.source_name == "ollama", f"{mid} must be on the local 'ollama' source"


def test_hex_models_are_free_owned_local():
    reg = default_registry()
    for mid in HEX_MODELS:
        assert reg.get(mid).dollars_per_unit == 0.0, f"{mid} must be $0 (owned-local)"


def test_hex_models_carry_difficulty_capable():
    reg = default_registry()
    assert reg.get("llama3.2:3b").difficulty_capable == "classify"
    assert reg.get("qwen2.5-coder:14b").difficulty_capable == "code"
    assert reg.get("deepseek-r1:14b").difficulty_capable == "code"


# ── analyst→code correction (so the local reasoner can serve it) ──────────────


def test_analyst_maps_to_code_not_design():
    """analyst is reasoning/research, not architecture — design stays the Claude-only top bucket."""
    assert task_class_to_difficulty("analyst") == "code"


# ── the selector routes tiers to Hex ──────────────────────────────────────────


def test_worker_routes_to_hex_when_available():
    engine = RulesEngine(_hex_sources(), default_registry(), list(_DEFAULT_RULES))
    decision = engine.route("worker")
    assert decision is not None
    assert decision.source.name == "ollama", f"worker should land on Hex, got {decision.source.name}"


def test_minion_routes_to_hex_when_available():
    engine = RulesEngine(_hex_sources(), default_registry(), list(_DEFAULT_RULES))
    assert engine.route("minion").source.name == "ollama"


def test_analyst_routes_to_hex_when_available():
    engine = RulesEngine(_hex_sources(), default_registry(), list(_DEFAULT_RULES))
    assert engine.route("analyst").source.name == "ollama"


def test_worker_falls_to_next_cheapest_when_hex_down():
    """Hex unreachable → not a branch: next-cheapest-capable (free_throttled) wins, no hard-fail."""
    engine = RulesEngine(_hex_sources(ollama_available=False), default_registry(), list(_DEFAULT_RULES))
    decision = engine.route("worker")
    assert decision is not None
    assert decision.source.name == "google_free"


# ── DS routes by the worker TIER, not an explicit model (tier-not-model) ──────


def test_ds_tier_cascade_uses_tier_not_explicit_model():
    from unseen_university.devices.dicksimnel.device import DickSimnelDevice
    builder = DickSimnelDevice._TIER_CASCADE[0]
    assert not builder[0], (
        "DS builder tier must route by the worker TIER (empty model_override), "
        f"not an explicit model — got {builder[0]!r}"
    )
