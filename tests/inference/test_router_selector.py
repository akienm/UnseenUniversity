"""
Tests for T-router-selector (increment 2 of D-inference-cost-optimizing-router):
the STATELESS cost-optimizing selector — two categorical filters (TIME eligibility x
DIFFICULTY capability) then argmin(cost_class_rank, dollars_per_unit, priority).

These assert the NEW routing behavior. Two changes are INTENTIONAL (advisor 2026-06-30):
  - foreground filters by TIME only (not cost inversion),
  - google_free (free_throttled) always beats ollama_cloud (subscription).
Failure-bump/escalation is OUT of scope here (T-router-failure-bump-escalation).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.rules_engine import (
    RoutingRule,
    RulesEngine,
    _DEFAULT_RULES,
)
from unseen_university.devices.inference.sources import Source, SourceRegistry


def _src(name, cost_class, *, available=True, time_bucket="interactive"):
    s = MagicMock(spec=Source)
    s.name = name
    s.available = available
    s.cost_class = cost_class
    s.time_bucket = time_bucket
    s.billing_type = "usage_based"
    return s


# ── argmin(dollars) within a cost_class ───────────────────────────────────────


def test_cheaper_dollars_wins_within_cost_class():
    """Same cost_class → argmin(dollars_per_unit), even when the pricier rule has better priority."""
    reg = SourceRegistry()
    reg.register(_src("or_a", "token_direct"))
    reg.register(_src("or_b", "token_direct"))
    models = ModelsRegistry([
        ModelSpec("cheap", "or_a", "worker", 0.05, 0.05, 8192),   # dollars 0.10
        ModelSpec("pricey", "or_b", "worker", 1.00, 1.00, 8192),  # dollars 2.00
    ])
    rules = [
        RoutingRule(1, "worker", "pricey", "or_b", "pricey-better-priority"),
        RoutingRule(9, "worker", "cheap", "or_a", "cheap-worse-priority"),
    ]
    decision = RulesEngine(reg, models, rules).route("worker")
    assert decision is not None
    assert decision.model.model_id == "cheap"


def test_cost_class_dominates_dollars():
    """A cheaper cost_class wins even if its marginal dollars are higher."""
    reg = SourceRegistry()
    reg.register(_src("local", "owned_local"))
    reg.register(_src("cloud", "token_direct"))
    models = ModelsRegistry([
        ModelSpec("local-m", "local", "worker", 0.0, 0.0, 8192),
        ModelSpec("cloud-m", "cloud", "worker", 0.01, 0.01, 8192),
    ])
    rules = [
        RoutingRule(1, "worker", "cloud-m", "cloud", "cloud"),
        RoutingRule(2, "worker", "local-m", "local", "local"),
    ]
    decision = RulesEngine(reg, models, rules).route("worker")
    assert decision.source.name == "local"


# ── TIME eligibility filter ───────────────────────────────────────────────────


def test_slow_source_excluded_under_interactive_urgency():
    """A cheap-but-overnight source is NOT a candidate for interactive work."""
    reg = SourceRegistry()
    reg.register(_src("slow_cheap", "owned_local", time_bucket="overnight"))
    reg.register(_src("fast_dear", "token_direct", time_bucket="interactive"))
    models = ModelsRegistry([
        ModelSpec("slow-m", "slow_cheap", "worker", 0.0, 0.0, 8192),
        ModelSpec("fast-m", "fast_dear", "worker", 1.0, 1.0, 8192),
    ])
    rules = [
        RoutingRule(1, "worker", "slow-m", "slow_cheap", "slow"),
        RoutingRule(2, "worker", "fast-m", "fast_dear", "fast"),
    ]
    engine = RulesEngine(reg, models, rules)
    # interactive urgency: the slow (cheaper) source is excluded → fast one wins
    assert engine.route("worker", foreground=True).source.name == "fast_dear"
    # batch urgency: the slow source re-enters and (cheaper) wins
    assert engine.route("worker", urgency="batch").source.name == "slow_cheap"


def test_foreground_filters_by_time_not_cost_inversion():
    """foreground=interactive picks the FAST source, not the expensive one (old behavior gone)."""
    reg = SourceRegistry()
    reg.register(_src("fast_cheap", "owned_local", time_bucket="interactive"))
    reg.register(_src("slow_dear", "token_direct", time_bucket="minutes"))
    models = ModelsRegistry([
        ModelSpec("fc", "fast_cheap", "worker", 0.0, 0.0, 8192),
        ModelSpec("sd", "slow_dear", "worker", 1.0, 1.0, 8192),
    ])
    rules = [
        RoutingRule(1, "worker", "fc", "fast_cheap", "fc"),
        RoutingRule(2, "worker", "sd", "slow_dear", "sd"),
    ]
    decision = RulesEngine(reg, models, rules).route("worker", foreground=True)
    assert decision.source.name == "fast_cheap"


# ── DIFFICULTY capability filter ──────────────────────────────────────────────


def test_below_difficulty_model_excluded():
    """A classify-only model is excluded from a design-difficulty task_class."""
    reg = SourceRegistry()
    reg.register(_src("weak", "owned_local"))
    reg.register(_src("strong", "token_direct"))
    models = ModelsRegistry([
        # designer task_class → required difficulty 'design'
        ModelSpec("weak-m", "weak", "designer", 0.0, 0.0, 8192, difficulty_capable="classify"),
        ModelSpec("strong-m", "strong", "designer", 1.0, 1.0, 8192, difficulty_capable="design"),
    ])
    rules = [
        RoutingRule(1, "designer", "weak-m", "weak", "weak"),
        RoutingRule(2, "designer", "strong-m", "strong", "strong"),
    ]
    decision = RulesEngine(reg, models, rules).route("designer")
    assert decision.model.model_id == "strong-m"


# ── availability (Hex-down is not a branch) ───────────────────────────────────


def test_source_absent_falls_to_next_cheapest():
    reg = SourceRegistry()
    reg.register(_src("local", "owned_local", available=False))  # Hex down
    reg.register(_src("cloud", "subscription", available=True))
    models = ModelsRegistry([
        ModelSpec("local-m", "local", "worker", 0.0, 0.0, 8192),
        ModelSpec("cloud-m", "cloud", "worker", 0.0, 0.0, 8192),
    ])
    rules = [
        RoutingRule(1, "worker", "local-m", "local", "local"),
        RoutingRule(2, "worker", "cloud-m", "cloud", "cloud"),
    ]
    decision = RulesEngine(reg, models, rules).route("worker")
    assert decision is not None
    assert decision.source.name == "cloud"


# ── taxonomy fix: google_free beats ollama_cloud ──────────────────────────────


def test_google_free_beats_ollama_cloud():
    """free_throttled (2) < subscription (3): the taxonomy fix landing."""
    reg = SourceRegistry()
    # ollama_cloud given the BETTER priority to prove cost_class, not priority, decides
    reg.register(_src("ollama_cloud", "subscription"))
    reg.register(_src("google_free", "free_throttled"))
    models = ModelsRegistry([
        ModelSpec("oc-m", "ollama_cloud", "worker", 0.0, 0.0, 8192),
        ModelSpec("gf-m", "google_free", "worker", 0.0, 0.0, 8192),
    ])
    rules = [
        RoutingRule(1, "worker", "oc-m", "ollama_cloud", "ollama-cloud-first"),
        RoutingRule(2, "worker", "gf-m", "google_free", "google-free-second"),
    ]
    decision = RulesEngine(reg, models, rules).route("worker")
    assert decision.source.name == "google_free"


# ── every production tier resolves ≥1 candidate (advisor point C) ─────────────


def test_every_production_tier_resolves_a_candidate():
    """The NEW difficulty filter must not strand any production tier."""
    from unseen_university.devices.inference.models_registry import default_registry as models_default
    reg = SourceRegistry()
    # make every source named in the default rules available + interactive
    seen = {r.source_name for r in _DEFAULT_RULES}
    for name in seen:
        cc = {"ollama": "owned_local", "google_free": "free_throttled",
              "ollama_cloud": "subscription"}.get(name, "token_direct")
        reg.register(_src(name, cc))
    models = models_default()
    engine = RulesEngine(reg, models)
    for tier in ("minion", "worker", "analyst", "designer"):
        assert engine.route(tier) is not None, f"tier {tier} stranded — no candidate resolves"


def test_sanity_worker_lands_on_google_free_pre_hex():
    """Live DS path: ollama_cloud unavailable (no key), google_free available → worker uses free tier."""
    reg = SourceRegistry()
    reg.register(_src("google_free", "free_throttled", available=True))
    reg.register(_src("ollama_cloud", "subscription", available=False))
    reg.register(_src("openrouter", "token_direct", available=True))
    models = ModelsRegistry([
        ModelSpec("gemini-2.5-flash", "google_free", "worker", 0.0, 0.0, 1_048_576),
        ModelSpec("devstral", "ollama_cloud", "worker", 0.0, 0.0, 128_000),
        ModelSpec("qwen-coder", "openrouter", "worker", 0.07, 0.28, 156_000),
    ])
    rules = [
        RoutingRule(1, "worker", "qwen-coder", "openrouter", "or"),
        RoutingRule(3, "worker", "gemini-2.5-flash", "google_free", "gf"),
        RoutingRule(10, "worker", "devstral", "ollama_cloud", "oc"),
    ]
    decision = RulesEngine(reg, models, rules).route("worker")
    assert decision.source.name == "google_free"


# ── DOMAIN capability filter (T-inference-domain-tag) ─────────────────────────


def test_domain_mismatch_model_excluded():
    """A prose-only model is excluded from a coding request; the coding model wins."""
    reg = SourceRegistry()
    reg.register(_src("prose_src", "owned_local"))   # cheaper — would win but for domain
    reg.register(_src("code_src", "token_direct"))
    models = ModelsRegistry([
        ModelSpec("prose-m", "prose_src", "worker", 0.0, 0.0, 8192, domains=["prose"]),
        ModelSpec("code-m", "code_src", "worker", 1.0, 1.0, 8192, domains=["coding"]),
    ])
    rules = [
        RoutingRule(1, "worker", "prose-m", "prose_src", "prose"),
        RoutingRule(2, "worker", "code-m", "code_src", "code"),
    ]
    decision = RulesEngine(reg, models, rules).route("worker", domain="coding")
    assert decision is not None
    assert decision.model.model_id == "code-m"


def test_generalist_model_selectable_for_any_domain():
    """A model with no domains (generalist) is eligible for a coding request."""
    reg = SourceRegistry()
    reg.register(_src("gen_src", "owned_local"))
    models = ModelsRegistry([
        ModelSpec("gen-m", "gen_src", "worker", 0.0, 0.0, 8192),  # domains=[] → generalist
    ])
    rules = [RoutingRule(1, "worker", "gen-m", "gen_src", "gen")]
    decision = RulesEngine(reg, models, rules).route("worker", domain="coding")
    assert decision is not None
    assert decision.model.model_id == "gen-m"


def test_generalist_request_matches_domain_specialized_model():
    """A request with domain='' (generalist) still selects a domain-tagged model."""
    reg = SourceRegistry()
    reg.register(_src("code_src", "owned_local"))
    models = ModelsRegistry([
        ModelSpec("code-m", "code_src", "worker", 0.0, 0.0, 8192, domains=["coding"]),
    ])
    rules = [RoutingRule(1, "worker", "code-m", "code_src", "code")]
    decision = RulesEngine(reg, models, rules).route("worker")  # domain='' default
    assert decision is not None
    assert decision.model.model_id == "code-m"


def test_crossing_record_includes_domain():
    """The routing crossing-log record names the requested domain (measurement signal)."""
    from unseen_university.devices.inference.routing_buckets import routing_crossing_record
    src = _src("code_src", "owned_local")
    model = ModelSpec("code-m", "code_src", "worker", 0.0, 0.0, 8192, domains=["coding"])
    rec = routing_crossing_record(src, model, "worker", "coding")
    assert rec["domain"] == "coding"


# ── dead source name reconciled ───────────────────────────────────────────────


def test_no_local_ollama_source_name_in_default_rules():
    assert all(r.source_name != "local_ollama" for r in _DEFAULT_RULES)
