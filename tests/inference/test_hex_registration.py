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


def test_ds_routes_by_tier_not_explicit_model():
    """DS routes by {domain, task_class} and never pins a model (tier-not-model contract).

    The old _TIER_CASCADE (empty model_override) is retired (T-router-failure-bump-escalation);
    the invariant now lives in the ToolLoop request itself: model='' (route by domain),
    task_class='worker', domain='coding'. An explicit model would bypass the cost-optimizing
    selector AND trip the pin-gate.
    """
    from unittest.mock import patch
    from unseen_university.devices.dicksimnel.toolloop import ToolLoop

    captured = {}

    class _Resp:
        text = "DONE: ok"
        tool_calls = None
        source_billing_type = "flat_rate"
        source_kind = "cloud"
        finish_reason = "stop"
        cost_estimate = 0.0
        input_tokens = 0
        output_tokens = 0
        model = "some-coding-model"

    class _Dev:
        def dispatch(self, req):
            captured["req"] = req
            return _Resp()

    with patch("unseen_university.devices.inference.device.InferenceDevice", return_value=_Dev()):
        ToolLoop().run({"id": "T-x", "description": "d", "tags": []}, "sys")

    req = captured["req"]
    assert req.model == "", f"DS must not pin a model — got {req.model!r}"
    assert req.task_class == "worker"
    assert req.domain == "coding"


# ── T-inference-coding-ladder-hex-cloud: the coding domain's full ladder ──────
#
# Hex-local first (owned_local, $0), Ollama-Cloud subscription only for the same-family
# flagships too large for Hex's 32GB RAM. No OR/Anthropic/Google on this domain's ladder.
#
# CRITICAL: DS always dispatches with task_class='worker' and relies on required_difficulty
# (the escalation-walk override) to reach the design rung. route() filters its candidate pool
# by rule.task_class == task_class BEFORE required_difficulty is ever considered — so a design-
# difficulty model filed under a 'designer'/'analyst' RoutingRule would be invisible to DS's
# actual call shape. These tests pin task_class='worker' throughout, matching DS.

CODING_LADDER_LOCAL = ("qwen3-coder:30b", "deepseek-r1:32b")
CODING_LADDER_CLOUD = ("qwen3-coder:480b-cloud", "deepseek-v3.1:671b-cloud")


def test_coding_ladder_local_models_registered_at_code_and_design():
    reg = default_registry()
    assert reg.get("qwen3-coder:30b").difficulty_capable == "code"
    assert reg.get("deepseek-r1:32b").difficulty_capable == "design"
    for mid in CODING_LADDER_LOCAL:
        spec = reg.get(mid)
        assert spec.source_name == "ollama"
        assert "coding" in spec.domains
        assert spec.dollars_per_unit == 0.0


def test_coding_ladder_cloud_models_registered_at_code_and_design():
    reg = default_registry()
    assert reg.get("qwen3-coder:480b-cloud").difficulty_capable == "code"
    assert reg.get("deepseek-v3.1:671b-cloud").difficulty_capable == "design"
    for mid in CODING_LADDER_CLOUD:
        spec = reg.get(mid)
        assert spec.source_name == "ollama_cloud"
        assert "coding" in spec.domains
        assert spec.dollars_per_unit == 0.0


def test_design_difficulty_coding_request_routes_to_hex_first():
    """The escalation walk sends task_class='worker' + required_difficulty='design' — this
    MUST land on the local architect (deepseek-r1:32b/ollama), not the cloud one, when Hex
    is up (cost_class=owned_local beats subscription)."""
    engine = RulesEngine(_hex_sources(), default_registry(), list(_DEFAULT_RULES))
    decision = engine.route("worker", domain="coding", required_difficulty="design")
    assert decision is not None
    assert decision.model.model_id == "deepseek-r1:32b"
    assert decision.source.name == "ollama"


def test_design_difficulty_hex_down_picks_cheapest_capable():
    """Intention: lowest cost with the required capabilities. Hex down → the cheapest
    remaining CAPABLE candidate wins. A capable generalist on a cheaper cost_class
    (gemini-2.5-flash / google_free, free_throttled) legitimately beats the coding
    domain's own subscription cloud flagship — cheaper + capable is the whole rule."""
    engine = RulesEngine(_hex_sources(ollama_available=False), default_registry(), list(_DEFAULT_RULES))
    decision = engine.route("worker", domain="coding", required_difficulty="design")
    assert decision is not None
    assert decision.source.name == "google_free"
    assert decision.model.model_id == "gemini-2.5-flash"


def test_design_difficulty_family_cloud_flagship_wins_when_it_is_cheapest_available():
    """With Hex AND the free generalist tier down, the coding domain's own cloud flagship
    (deepseek-v3.1:671b-cloud, subscription) is the cheapest capable candidate left — proving
    the cloud rung is reachable and correctly ordered when nothing cheaper is available."""
    reg = SourceRegistry()
    reg.register(_src("ollama", "owned_local", available=False))
    reg.register(_src("google_free", "free_throttled", available=False))
    reg.register(_src("ollama_cloud", "subscription"))
    engine = RulesEngine(reg, default_registry(), list(_DEFAULT_RULES))
    decision = engine.route("worker", domain="coding", required_difficulty="design")
    assert decision is not None
    assert decision.model.model_id == "deepseek-v3.1:671b-cloud"
    assert decision.source.name == "ollama_cloud"


def test_code_difficulty_coding_request_still_prefers_cheapest_hex_coder():
    """Registering the bigger local/cloud coders must not disturb the existing code-tier
    preference for the cheapest available Hex coder."""
    engine = RulesEngine(_hex_sources(), default_registry(), list(_DEFAULT_RULES))
    decision = engine.route("worker", domain="coding", required_difficulty="code")
    assert decision is not None
    assert decision.source.name == "ollama"


def test_design_difficulty_models_only_reachable_via_worker_task_class():
    """Regression pin for the wiring gap this ticket fixed: filing the new rules under
    task_class='worker' (matching DS) is what makes them reachable at all — proves the
    routing rules exist under 'worker', not a dead 'designer'/'analyst' rule DS never sends."""
    worker_rules = [r for r in _DEFAULT_RULES if r.task_class == "worker"]
    worker_model_ids = {r.model_id for r in worker_rules}
    assert "deepseek-r1:32b" in worker_model_ids
    assert "deepseek-v3.1:671b-cloud" in worker_model_ids
