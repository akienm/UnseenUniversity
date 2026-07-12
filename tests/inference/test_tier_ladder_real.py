"""Proof for T-inference-tier-ladder-real (D-domains-general-with-device-owned-specializations).

THE DEFECT (verified 2026-07-08): the role ladder's rungs were not real. Five role tiers
(apprentice < builder < creator < master < guru) collapsed onto three difficulty buckets —
builder AND creator both seeded 'code' (→ the SAME model), master AND guru both seeded
'design', and master resolved to None. An escalation policy had nothing to walk: asking for a
"creator stack" returned the same model as a "builder stack".

THE FIX has two halves, both proven here against a hermetic rack (no live inference):

  1. INJECTIVE PROJECTION (dimensions._TIER_DIFFICULTY): the four MODEL rungs map one-to-one
     onto the four MEASURED difficulty buckets — apprentice→classify, builder→code,
     creator→design, master→frontier. guru is the HUMAN terminal (Akien), not a model rung, so
     resolve() short-circuits it to rules_engine.HUMAN_TERMINAL, strictly above master.

  2. LEAST-OVER-PROVISIONED SELECTOR (rules_engine.resolve sort key): among EQUAL-COST
     candidates, prefer the lowest bucket that still clears the floor. Without this, a
     $0-heavy local registry breaks ties by model-name spelling, so raising the floor does
     NOT change the pick (measured: apprentice and builder both landed on the same
     alphabetically-first $0 model). Cost still dominates — cheapest wins first — so the cloud
     fleet is not re-stranded.

Together: raising the rung strictly raises the SELECTED model's capability bucket. The proof
asserts BUCKETS (the real invariant), not model_ids (which rot when the registry changes). The
adversarial model naming below (capability order ≠ alphabetical order) is what makes the pre-
fix behavior authentically red: the old alphabetical tiebreak would pick the wrong bucket.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from unseen_university.devices.inference.connections import Connection, ConnectionsRegistry
from unseen_university.devices.inference.dimensions import RouteRequest
from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.routing_buckets import DIFFICULTY_BUCKETS
from unseen_university.devices.inference.rules_engine import RulesEngine
from unseen_university.devices.inference.sources import Source, SourceRegistry

# NB: HUMAN_TERMINAL is imported LOCALLY inside test_guru_* (not at module scope) so this
# file still COLLECTS when the impl is reverted for a proof-emitter red run — the ladder-
# distinctness proof node below asserts on behavior, and must not be masked by a collateral
# ImportError of a symbol the impl introduces (reference_proof_emitter_gotchas).

_MODEL_RUNGS = ("apprentice", "builder", "creator", "master")
_EXPECTED_BUCKET = {
    "apprentice": "classify",
    "builder": "code",
    "creator": "design",
    "master": "frontier",
}


def _rank(bucket: str) -> int:
    return DIFFICULTY_BUCKETS.index(bucket)


def _src(name):
    s = MagicMock(spec=Source)
    s.name = name
    s.available = True
    s.cost_class = "owned_local"   # all rungs on ONE $0 owned box → equal cost, so the
    s.time_bucket = "interactive"  # proximity tiebreak (not spelling) must do the work.
    s.billing_type = "usage_based"
    return s


def _ladder_rack() -> RulesEngine:
    """A coding rack with exactly ONE $0 model per measured bucket. Model names are chosen so
    ALPHABETICAL order != CAPABILITY order (aaa-code sorts before bbb-classify), so the pre-fix
    alphabetical tiebreak would pick the wrong bucket — the authentic red."""
    sources = SourceRegistry()
    sources.register(_src("hex"))
    models = ModelsRegistry([
        ModelSpec("aaa-code", "worker", 0.0, 0.0, 8192,
                  difficulty_capable="code", features=["tools"], domains=["coding"]),
        ModelSpec("bbb-classify", "minion", 0.0, 0.0, 8192,
                  difficulty_capable="classify", features=["tools"], domains=["coding"]),
        ModelSpec("ccc-design", "analyst", 0.0, 0.0, 8192,
                  difficulty_capable="design", features=["tools"], domains=["coding"]),
        ModelSpec("ddd-frontier", "analyst", 0.0, 0.0, 8192,
                  difficulty_capable="frontier", features=["tools"], domains=["coding"]),
    ])
    conns = ConnectionsRegistry()
    for mid in ("aaa-code", "bbb-classify", "ccc-design", "ddd-frontier"):
        conns.register(Connection(mid, "hex", 0.0))
    # policies=[] → envelope is seed+domain only (isolate the projection→selector path).
    return RulesEngine(sources, models, connections=conns, policies=[])


def _resolve(eng, tier):
    # No required_difficulty override → the pick is pinned to the SEED floor: a deterministic
    # single pick per rung, so this proves the SEED ladder (not the escalation walk).
    return eng.resolve(RouteRequest(
        ticket_tier=tier, builder_tier="builder", domain="coding",
    ))


def test_ladder_rungs_are_real_and_strictly_increasing():
    """Each model rung selects a strictly-higher MEASURED bucket than the one below; the named
    builder≠creator defect is fixed; guru is the human terminal above master."""
    eng = _ladder_rack()
    decs = {t: _resolve(eng, t) for t in _MODEL_RUNGS}

    # Every model rung resolves to a model at its EXACT expected bucket (the projection +
    # least-over-provisioned selector together pick just-enough capability).
    for tier in _MODEL_RUNGS:
        d = decs[tier]
        assert d is not None and d.model is not None, f"{tier} must resolve to a model"
        assert d.model.difficulty_bucket == _EXPECTED_BUCKET[tier], (
            f"{tier} resolved to bucket {d.model.difficulty_bucket!r}, "
            f"expected {_EXPECTED_BUCKET[tier]!r}"
        )

    # STRICTLY increasing across the model rungs — no rung identical to the one below it.
    ranks = [_rank(decs[t].model.difficulty_bucket) for t in _MODEL_RUNGS]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks), (
        f"model rungs must be strictly increasing in capability, got buckets "
        f"{[decs[t].model.difficulty_bucket for t in _MODEL_RUNGS]}"
    )

    # The ticket's NAMED assertion: builder and creator resolve to DIFFERENT models AND a
    # strictly higher bucket for creator (was identical — both 'code').
    assert decs["builder"].model.model_id != decs["creator"].model.model_id
    assert _rank(decs["creator"].model.difficulty_bucket) > _rank(decs["builder"].model.difficulty_bucket)


def test_guru_is_the_human_terminal_above_master():
    """guru is Akien (human-only) — no model stands at the top rung. It resolves to the
    HUMAN_TERMINAL sentinel (model is None), strictly above master's frontier model, so the
    ladder is monotone all the way up without a phantom top model."""
    from unseen_university.devices.inference.rules_engine import HUMAN_TERMINAL

    eng = _ladder_rack()
    guru = _resolve(eng, "guru")
    master = _resolve(eng, "master")
    assert guru is HUMAN_TERMINAL
    assert guru.model is None and guru.is_human_terminal
    # Distinct from master (a real frontier model) — the top pair is not identical either.
    assert master is not None and master.model is not None
    assert guru is not master
