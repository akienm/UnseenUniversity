"""
The cost-optimizing selector (D-inference-cost-optimizing-router), exercised through the
dimensional resolver.

These tests were migrated from the pre-cutover route() suite at
T-inference-migrate-consumers-cutover. The selector itself is UNCHANGED — only its INPUT
moved from hardcoded (task_class, model_id, source_name) triples to connection candidates.
What is asserted here is the selector's contract, which the cutover must preserve:

  - cost_class dominates marginal dollars (owned-local hardware beats a cheaper metered call)
  - within a cost_class, the cheaper per-connection dollars wins
  - a domain-specialized model is excluded from a mismatched domain; a generalist model
    (domains=[]) matches any domain; a generalist REQUEST (domain='') matches a specialized model
  - every production ticket_tier still resolves a candidate (no tier stranded by the cutover)
  - the routing crossing record carries the domain (observability at the interface)

Redundant-with-resolve() route() tests (urgency/time filter, availability skip, difficulty
floor) were deleted rather than migrated — that coverage lives in test_resolver_compose.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from unseen_university.devices.inference.connections import Connection, ConnectionsRegistry
from unseen_university.devices.inference.dimensions import ROLE_TIERS, RouteRequest
from unseen_university.devices.inference.device import _default_models, _default_sources
from unseen_university.devices.inference.connections import default_connections
from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.rules_engine import RulesEngine
from unseen_university.devices.inference.routing_buckets import routing_crossing_record
from unseen_university.devices.inference.sources import Source, SourceRegistry


def _src(name, cost_class, *, available=True, time_bucket="interactive"):
    s = MagicMock(spec=Source)
    s.name = name
    s.available = available
    s.cost_class = cost_class
    s.time_bucket = time_bucket
    s.billing_type = "usage_based"
    return s


def _conns(*edges) -> ConnectionsRegistry:
    reg = ConnectionsRegistry()
    for model_id, source_name, dollars in edges:
        reg.register(Connection(model_id, source_name, dollars))
    return reg


def _engine(sources, models, conns):
    return RulesEngine(sources, models, connections=conns, policies=[])


def _req(domain="", tier="builder"):
    return RouteRequest(
        ticket_tier=tier, builder_tier="builder", domain=domain, urgency="normal"
    )


# ── cost ordering ─────────────────────────────────────────────────────────────


def test_cheaper_dollars_wins_within_cost_class():
    """Same cost_class → the cheaper per-CONNECTION marginal dollars wins."""
    sources = SourceRegistry()
    sources.register(_src("or_a", "token_direct"))
    sources.register(_src("or_b", "token_direct"))
    models = ModelsRegistry([
        ModelSpec("cheap", "worker", 0.05, 0.05, 8192),
        ModelSpec("pricey", "worker", 1.00, 1.00, 8192),
    ])
    conns = _conns(("cheap", "or_a", 0.10), ("pricey", "or_b", 2.00))
    dec = _engine(sources, models, conns).resolve(_req())
    assert dec is not None
    assert dec.model.model_id == "cheap"


def test_cost_class_dominates_dollars():
    """An owned-local connection beats a cheaper-per-token metered one: cost_class is the
    FIRST sort key (owned hardware has no marginal call cost that dollars can express)."""
    sources = SourceRegistry()
    sources.register(_src("local", "owned_local"))
    sources.register(_src("cloud", "token_direct"))
    models = ModelsRegistry([
        ModelSpec("local-m", "worker", 5.0, 5.0, 8192),   # dearer per token...
        ModelSpec("cloud-m", "worker", 0.01, 0.01, 8192),  # ...but cloud is metered
    ])
    conns = _conns(("local-m", "local", 10.0), ("cloud-m", "cloud", 0.02))
    dec = _engine(sources, models, conns).resolve(_req())
    assert dec is not None
    assert dec.source.name == "local"  # cost_class dominates the cheaper dollars


# ── domain eligibility (both directions) ──────────────────────────────────────


def test_domain_mismatch_model_excluded():
    sources = SourceRegistry()
    sources.register(_src("prose_src", "owned_local"))
    sources.register(_src("code_src", "token_direct"))
    models = ModelsRegistry([
        ModelSpec("prose-m", "worker", 0.0, 0.0, 8192, domains=["prose"]),
        ModelSpec("code-m", "worker", 1.0, 1.0, 8192, domains=["coding"]),
    ])
    conns = _conns(("prose-m", "prose_src", 0.0), ("code-m", "code_src", 2.0))
    dec = _engine(sources, models, conns).resolve(_req(domain="coding"))
    assert dec is not None
    # prose-m is cheaper AND owned_local, but wrong domain → excluded
    assert dec.model.model_id == "code-m"


def test_generalist_model_selectable_for_any_domain():
    """A model with domains=[] is a generalist: eligible for any requested domain."""
    sources = SourceRegistry()
    sources.register(_src("gen_src", "owned_local"))
    models = ModelsRegistry([ModelSpec("gen-m", "worker", 0.0, 0.0, 8192)])  # domains=[]
    conns = _conns(("gen-m", "gen_src", 0.0))
    dec = _engine(sources, models, conns).resolve(_req(domain="coding"))
    assert dec is not None
    assert dec.model.model_id == "gen-m"


def test_generalist_request_matches_domain_specialized_model():
    """A generalist REQUEST (domain='') matches a domain-specialized model."""
    sources = SourceRegistry()
    sources.register(_src("code_src", "owned_local"))
    models = ModelsRegistry([
        ModelSpec("code-m", "worker", 0.0, 0.0, 8192, domains=["coding"]),
    ])
    conns = _conns(("code-m", "code_src", 0.0))
    dec = _engine(sources, models, conns).resolve(_req(domain=""))
    assert dec is not None
    assert dec.model.model_id == "code-m"


# ── no tier is stranded by the cutover ────────────────────────────────────────


def test_every_production_tier_resolves_a_candidate():
    """Against the REAL default rack, every ticket_tier resolves a candidate.

    The cutover replaced per-task_class curated triples with dimensional selection; this is
    the guard that no tier was left with an empty candidate pool (the failure a triple-to-
    dimension migration can silently introduce).
    """
    sources = _default_sources()
    models = _default_models()
    engine = RulesEngine(
        sources, models, connections=default_connections(models), policies=[]
    )
    for tier in ROLE_TIERS:
        req = RouteRequest(
            ticket_tier=tier, builder_tier="builder", domain="", urgency="normal",
        )  # no required_difficulty override → deterministic seed pick, no walk
        assert engine.resolve(req) is not None, f"tier {tier} stranded — no candidate resolves"


# ── observability at the interface ────────────────────────────────────────────


def test_crossing_record_includes_domain():
    source = _src("s", "owned_local")
    model = ModelSpec("code-m", "worker", 0.0, 0.0, 8192, domains=["coding"])
    rec = routing_crossing_record(source, model, "builder", "coding")
    assert rec["domain"] == "coding"
    assert rec["model"] == "code-m"
    assert rec["source"] == "s"
