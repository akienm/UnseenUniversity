"""
Escalation must be able to LEAVE the local box.

T-inference-cost-first-sort-strands-cloud-fleet.

The defect, measured 2026-07-09 over all 450 dimension combinations (5 ticket_tier x 5
builder_tier x 2 domain x 3 urgency x 3 required_difficulty):

    REACHABLE:  deepseek-r1:32b @ollama, deepseek-r1:14b @ollama, devstral-small-2:24b @ollama
    UNREACHABLE: 19 of 22 models — the ENTIRE cloud fleet, including claude-opus-4.8,
                 claude-sonnet-4.6, gemini-2.5-flash, deepseek-v3.1:671b-cloud and
                 qwen3-coder:480b-cloud (the last two on a subscription already paid for).

Mechanism: the selector sorts by `cost_class_rank` FIRST, and `difficulty_meets` is a `>=`
filter. Any model on `ollama` (cost_class `owned_local`, rank 1) therefore dominates every
bucket it CLAIMS, and also every bucket beneath. Escalation only ever raised the required
bucket — which narrows candidates but never changes the cost preference. With three buckets
and a local model in each, the walk was:

    deepseek-r1:14b -> deepseek-r1:32b -> capability-ceiling alarm

Two local rungs, then halt. The alarm was a true statement about the reachable set and a false
statement about the fleet.

Cost-first is NOT the bug — "cheapest model that clears the required capability" is exactly
what we want. The bug is that capability had three levels, all of them claimed by local
models, and nothing verified the claim. `frontier` is the rung the local box does not hold,
and a model may only claim it with measured evidence (test_capability_evidence.py).

HERMETIC: a synthetic rack. The real source registry probes credentials at construction
(`anthropic` reports available=False on this box), so a sweep over it would pass or fail on
the environment — the "green on the weather" failure of 2026-07-08.
"""

from __future__ import annotations

import itertools

import pytest

from unseen_university.devices.inference.capability_evidence import measured
from unseen_university.devices.inference.connections import Connection, ConnectionsRegistry
from unseen_university.devices.inference.dimensions import ROLE_TIERS, RouteRequest
from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.routing_buckets import (
    DIFFICULTY_BUCKETS,
    TOP_DIFFICULTY,
    URGENCY_LEVELS,
    bump_difficulty,
)
from unseen_university.devices.inference.rules_engine import RulesEngine


class _Source:
    """A provider. Availability is set, not probed — that is what makes this hermetic."""

    def __init__(self, name, cost_class, time_bucket="interactive"):
        self.name = name
        self.cost_class = cost_class
        self.time_bucket = time_bucket
        self.available = True


def _rack():
    """The rack in miniature: a free local box that claims two rungs, and a paid cloud model
    that holds the rung above them. Exactly the shape of the real one."""
    sources = _SourceRegistry([
        _Source("hex", "owned_local"),          # rank 1 — always cheapest
        _Source("cloud", "subscription"),       # rank 3
    ])
    # NB the two local models are both $0 on the same cost_class, so the selector's final
    # tiebreak is `model_id`, LEXICALLY. Named to mirror the real rack, where the same tiebreak
    # happens to order 'deepseek-r1:14b' before 'deepseek-r1:32b' — the cheaper box wins the
    # cheaper rung by alphabetical luck, not by design. (Noticed here; not this ticket's fix.)
    models = ModelsRegistry([
        ModelSpec("local-1-small", "worker", 0.0, 0.0, 8192, difficulty_capable="code"),
        ModelSpec("local-2-big", "analyst", 0.0, 0.0, 8192, difficulty_capable="design"),
        ModelSpec("cloud-frontier", "analyst", 0.0, 0.0, 8192,
                  difficulty_capable=TOP_DIFFICULTY,
                  capability_evidence=measured("synthetic-rack", ceiling_tokens=8192, samples=2)),
    ])
    conns = ConnectionsRegistry()
    conns.register(Connection("local-1-small", "hex", 0.0))
    conns.register(Connection("local-2-big", "hex", 0.0))
    conns.register(Connection("cloud-frontier", "cloud", 0.0))
    return RulesEngine(sources, models, connections=conns, policies=[]), models


class _SourceRegistry:
    def __init__(self, sources):
        self._by_name = {s.name: s for s in sources}

    def get(self, name):
        return self._by_name.get(name)

    def all(self):
        return list(self._by_name.values())

    def all_available(self):
        return [s for s in self._by_name.values() if s.available]


def _resolve(engine, required, tier="builder"):
    return engine.resolve(
        RouteRequest(ticket_tier=tier, builder_tier="builder", domain=""),
        required_difficulty=required,
    )


def test_the_cheap_local_model_still_wins_the_rungs_it_can_serve():
    """Cost-first is not the bug. Do not 'fix' it by preferring expensive models."""
    engine, _ = _rack()
    assert _resolve(engine, "code").model.model_id == "local-1-small"
    assert _resolve(engine, "design").model.model_id == "local-2-big"


def test_escalating_past_the_top_local_rung_reaches_a_non_local_source():
    """THE test. Two capability bumps from 'code' must leave the box."""
    engine, _ = _rack()
    required = bump_difficulty("code", 2)
    assert required == TOP_DIFFICULTY, "the ladder must have a rung above the local box"

    decision = _resolve(engine, required)
    assert decision is not None, (
        "escalating past the top local rung resolved to NOTHING — the walk raises a "
        "capability-ceiling alarm while a more capable model sits unreachable on the rack"
    )
    assert decision.model.model_id == "cloud-frontier"
    assert decision.source.name == "cloud"
    assert decision.source.cost_class != "owned_local", (
        "escalation never left the local box — the defect this ticket exists to fix"
    )


def test_the_walk_still_terminates_above_the_frontier():
    assert bump_difficulty("code", len(DIFFICULTY_BUCKETS)) is None


def test_a_frontier_capable_model_does_not_capture_the_cheaper_rungs_by_being_cheaper():
    """A `>=` capability filter means a frontier model is eligible for 'code' too. It must only
    win there if it is genuinely cheaper — otherwise the top rung's model serves every call and
    the cost optimizer is dead."""
    engine, _ = _rack()
    assert _resolve(engine, "code").source.name == "hex"


def test_the_sweep_reaches_more_than_the_local_box():
    """The ticket's stated green: the dimension sweep must reach more than the local models."""
    engine, _ = _rack()
    reached = set()
    for tt, bt, urg, rd in itertools.product(
        ROLE_TIERS, ROLE_TIERS, URGENCY_LEVELS, DIFFICULTY_BUCKETS
    ):
        d = engine.resolve(
            RouteRequest(ticket_tier=tt, builder_tier=bt, domain="", urgency=urg),
            required_difficulty=rd,
        )
        # A guru request resolves to the HUMAN_TERMINAL (model is None) — a hand-off to a
        # person, not a model reach — so it does not count toward the reachable model set.
        if d and d.model is not None:
            reached.add((d.model.model_id, d.source.name))
    sources_reached = {src for _, src in reached}
    assert "cloud" in sources_reached, (
        f"no dimension combination reaches a non-local source; reached={sorted(reached)}"
    )
    assert len(reached) > 2, f"the sweep reaches only {sorted(reached)}"


@pytest.mark.parametrize("hop,expected", [(0, "code"), (1, "design"), (2, TOP_DIFFICULTY)])
def test_the_escalation_ladder_has_three_distinct_rungs(hop, expected):
    assert bump_difficulty("code", hop) == expected


# ── the REAL rack, not a fixture ──────────────────────────────────────────────


def _real_engine():
    """The shipped registries, with provider availability FORCED ON.

    Availability is a credential/health fact about this box today (`anthropic` is down here);
    reachability is a routing fact. Forcing availability isolates the second from the first, so
    this test measures the ROUTER and cannot pass or fail on the weather.
    """
    from unseen_university.devices.inference.connections import default_connections
    from unseen_university.devices.inference.models_registry import default_registry as models
    from unseen_university.devices.inference.sources import default_registry as sources

    m, s = models(), sources()
    for src in s.all():
        src.available = True
    return RulesEngine(s, m, connections=default_connections(m), policies=None)


def test_the_real_rack_can_escalate_off_the_local_box():
    """THE ticket's green. Before `frontier`, no dimension combination reached a cloud model.

    A generalist request escalated twice from 'code' must resolve to a non-`owned_local`
    source. It resolves to deepseek-v3.1:671b-cloud on ollama_cloud — the Ollama Pro
    subscription that was already being paid for and that dimensional routing never used.
    """
    engine = _real_engine()
    top = bump_difficulty("code", 2)
    decision = engine.resolve(
        RouteRequest(ticket_tier="builder", builder_tier="builder", domain=""),
        required_difficulty=top,
    )
    assert decision is not None, (
        f"a generalist request at the top capability rung ({top!r}) resolves to nothing — "
        f"escalation halts with a capability-ceiling alarm while more capable models sit on "
        f"the rack, unreachable"
    )
    assert decision.source.cost_class != "owned_local", (
        f"the top rung resolved back onto the local box ({decision.model.model_id}@"
        f"{decision.source.name}) — escalation still cannot leave it"
    )


def test_the_real_ladder_has_three_distinct_generalist_rungs():
    """Each rung must select a DIFFERENT model. Two rungs resolving identically is a fake ladder."""
    engine = _real_engine()
    picked = []
    for hop in range(3):
        d = engine.resolve(
            RouteRequest(ticket_tier="builder", builder_tier="builder", domain=""),
            required_difficulty=bump_difficulty("code", hop),
        )
        assert d is not None, f"rung {hop} resolves to nothing"
        picked.append(d.model.model_id)
    assert len(set(picked)) == 3, f"the rungs are not distinct: {picked}"


def test_the_coding_ladder_still_tops_out_and_that_is_not_hidden():
    """PINS A KNOWN GAP, so it cannot be mistaken for a working coding ladder.

    The `coding-needs-tools` policy requires `features=['tools']`, and no design- or
    frontier-capable coding model declares it. So coding escalation is ONE rung
    (devstral-small-2:24b) and then nothing.

    The fix is NOT to add `features=['tools']` to deepseek-v3.1:671b-cloud on a hunch — that is
    the unmeasured overclaim this whole ticket exists to eliminate, and it is precisely how the
    coding ladder was broken before (a compensation folded back without proving its property).
    Measure the model's tool-calling, then declare it. => T-inference-activate-coding-tools-policy
    """
    engine = _real_engine()
    for hop in (1, 2):
        d = engine.resolve(
            RouteRequest(ticket_tier="builder", builder_tier="builder", domain="coding"),
            required_difficulty=bump_difficulty("code", hop),
        )
        assert d is None, (
            f"coding now resolves at rung {hop} ({d.model.model_id}) — if a tool-capable "
            f"coding model was MEASURED and added, delete this test; if a `tools` flag was "
            f"asserted without measurement, revert it"
        )
