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

from unseen_university.devices.inference.connections import default_connections
from unseen_university.devices.inference.dimensions import RouteRequest
from unseen_university.devices.inference.models_registry import default_registry
from unseen_university.devices.inference.routing_buckets import task_class_to_difficulty
from unseen_university.devices.inference.rules_engine import RulesEngine
from unseen_university.devices.inference.sources import Source, SourceRegistry

HEX_MODELS = ("llama3.2:3b", "qwen2.5-coder:14b", "deepseek-r1:14b")

# The mechanical task_class → ticket_tier(role) bridge the resolver consumes (mirrors
# domains.base._TASK_CLASS_TO_TIER): minion→apprentice, worker/analyst→builder, designer→master.
_TIER = {"minion": "apprentice", "worker": "builder", "analyst": "builder", "designer": "master"}


def _resolve(engine, task_class, *, domain="", required_difficulty=""):
    """Bridge the old route(task_class, ...) call shape onto resolve(RouteRequest)."""
    req = RouteRequest(
        ticket_tier=_TIER[task_class], builder_tier="builder", domain=domain
    )
    return engine.resolve(req, required_difficulty=required_difficulty)


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
    conns = default_connections(reg)
    for mid in HEX_MODELS:
        spec = reg.get(mid)
        assert spec is not None, f"{mid} not registered"
        # Reachability moved off ModelSpec.source_name onto the connections stack.
        srcs = {c.source_name for c in conns.by_model(mid)}
        assert "ollama" in srcs, f"{mid} must be reachable on the local 'ollama' source"


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
    engine = RulesEngine(_hex_sources(), default_registry())
    decision = _resolve(engine, "worker")
    assert decision is not None
    assert decision.source.name == "ollama", f"worker should land on Hex, got {decision.source.name}"


def test_minion_routes_to_hex_when_available():
    engine = RulesEngine(_hex_sources(), default_registry())
    assert _resolve(engine, "minion").source.name == "ollama"


def test_analyst_routes_to_hex_when_available():
    engine = RulesEngine(_hex_sources(), default_registry())
    assert _resolve(engine, "analyst").source.name == "ollama"


def test_worker_falls_to_next_cheapest_when_hex_down():
    """Hex unreachable → not a branch: next-cheapest-capable (free_throttled) wins, no hard-fail."""
    engine = RulesEngine(_hex_sources(ollama_available=False), default_registry())
    decision = _resolve(engine, "worker")
    assert decision is not None
    assert decision.source.name == "google_free"


# ── DS routes by the worker TIER, not an explicit model (tier-not-model) ──────


def test_ds_routes_by_tier_not_explicit_model():
    """DS routes by {domain, task_class} and never pins a model (tier-not-model contract).

    The old _TIER_CASCADE (empty model_override) is retired (T-router-failure-bump-escalation);
    the invariant now lives in the coding domain's shared-loop request: model='' (route by
    domain), task_class='worker', domain='coding'. An explicit model would bypass the
    cost-optimizing selector AND trip the pin-gate. Driven through the real DS path
    (CodingDomain.run → shared AgenticLoop).
    """
    from unittest.mock import patch
    from unseen_university.devices.inference.domains.coding import CodingDomain

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

    with patch("unseen_university.devices.inference.device.InferenceDevice", return_value=_Dev()), \
         patch("unseen_university.devices.inference.domains.coding._orientation_prefix", return_value=""):
        CodingDomain().run({"id": "T-x", "description": "d", "tags": []})

    req = captured["req"]
    assert req.model == "", f"DS must not pin a model — got {req.model!r}"
    assert req.task_class == "worker"
    assert req.domain == "coding"


# ── T-inference-coding-ladder-hex-cloud: the coding domain's full ladder ──────
#
# Hex-local first (owned_local, $0), Ollama-Cloud subscription only for the same-family
# flagships too large for Hex's 32GB RAM. No OR/Anthropic/Google on this domain's ladder.
#
# CRITICAL: DS always dispatches with task_class='worker' (→ ticket_tier='builder') and relies
# on required_difficulty (the escalation-walk override) to reach the design rung. The resolver
# filters candidate MODELS by the capability envelope (difficulty floor + domain), not by any
# per-task_class rule pool — required_difficulty raises the floor so a design-capable coding
# model becomes reachable. These tests pin task_class='worker' throughout, matching DS.

CODING_LADDER_LOCAL = ("qwen3-coder:30b", "deepseek-r1:32b")
CODING_LADDER_CLOUD = ("qwen3-coder:480b-cloud", "deepseek-v3.1:671b-cloud")


def test_coding_ladder_local_models_registered_at_code_and_design():
    reg = default_registry()
    conns = default_connections(reg)
    assert reg.get("qwen3-coder:30b").difficulty_capable == "code"
    assert reg.get("deepseek-r1:32b").difficulty_capable == "design"
    for mid in CODING_LADDER_LOCAL:
        spec = reg.get(mid)
        assert "ollama" in {c.source_name for c in conns.by_model(mid)}
        assert "coding" in spec.domains
        assert spec.dollars_per_unit == 0.0


def test_coding_ladder_cloud_models_registered_at_code_and_design():
    reg = default_registry()
    conns = default_connections(reg)
    assert reg.get("qwen3-coder:480b-cloud").difficulty_capable == "code"
    # 'design' here was a DECLARED claim. Measured 2026-07-09: deepseek-v3.1:671b-cloud is the
    # only model with evidence of clearing the frontier band (4/4, including b5-frobenius where
    # the local deepseek-r1:32b answers 23 against a ground truth of 43). It is now the top rung
    # of the escalation ladder — and the reason escalation can leave the local box at all.
    spec = reg.get("deepseek-v3.1:671b-cloud")
    assert spec.difficulty_capable == "frontier"
    assert spec.capability_evidence.startswith("measured:")
    for mid in CODING_LADDER_CLOUD:
        spec = reg.get(mid)
        assert "ollama_cloud" in {c.source_name for c in conns.by_model(mid)}
        assert "coding" in spec.domains
        assert spec.dollars_per_unit == 0.0


def test_design_difficulty_coding_request_routes_to_hex_first():
    """The escalation walk sends task_class='worker' + required_difficulty='design' — this
    MUST land on the local architect (deepseek-r1:32b/ollama), not the cloud one, when Hex
    is up (cost_class=owned_local beats subscription)."""
    # policies=[] isolates the difficulty/cost LADDER (what this test pins) from the
    # coding-needs-tools feature policy: the design-capable coding models carry no 'tools'
    # feature, so under _DEFAULT_POLICIES a design+coding request is a no-capable-connection
    # (a separate policy/data concern, not the ladder ordering under test here).
    # NOTE: policies=[] here is TEST ISOLATION, not the production config — device.py wires
    # policies=None (_DEFAULT_POLICIES) so coding requires a tool-capable model. The design
    # rung genuinely has no tool-capable coder locally: that honest ceiling is asserted in
    # test_consumers_cutover.py, and the walk's misclassification of it is tracked in
    # T-inference-capability-ceiling-misclassified-as-availability.
    engine = RulesEngine(_hex_sources(), default_registry(), policies=[])
    decision = _resolve(engine, "worker", domain="coding", required_difficulty="design")
    assert decision is not None
    assert decision.model.model_id == "deepseek-r1:32b"
    assert decision.source.name == "ollama"


def test_design_difficulty_hex_down_picks_cheapest_capable():
    """Intention: lowest cost with the required capabilities. Hex down → the cheapest
    remaining CAPABLE candidate wins. A capable generalist on a cheaper cost_class
    (gemini-2.5-flash / google_free, free_throttled) legitimately beats the coding
    domain's own subscription cloud flagship — cheaper + capable is the whole rule."""
    # policies=[] isolates the ladder from coding-needs-tools (see the hex-first test).
    engine = RulesEngine(_hex_sources(ollama_available=False), default_registry(), policies=[])
    decision = _resolve(engine, "worker", domain="coding", required_difficulty="design")
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
    # policies=[] isolates the ladder from coding-needs-tools (see the hex-first test).
    engine = RulesEngine(reg, default_registry(), policies=[])
    decision = _resolve(engine, "worker", domain="coding", required_difficulty="design")
    assert decision is not None
    assert decision.model.model_id == "deepseek-v3.1:671b-cloud"
    assert decision.source.name == "ollama_cloud"


def test_code_difficulty_coding_request_still_prefers_cheapest_hex_coder():
    """Registering the bigger local/cloud coders must not disturb the existing code-tier
    preference for the cheapest available Hex coder."""
    engine = RulesEngine(_hex_sources(), default_registry())
    decision = _resolve(engine, "worker", domain="coding", required_difficulty="code")
    assert decision is not None
    assert decision.source.name == "ollama"


# test_design_difficulty_models_only_reachable_via_worker_task_class is retired: it pinned
# the old RoutingRule table's task_class filing (r.task_class == "worker"). Rules and their
# task_class filing are deleted at the cutover — models are now selected by difficulty/domain
# capability, not by a per-task_class rule pool — so there is no such invariant to pin. The
# design-difficulty reachability it guarded is covered by the resolve() tests just above.
