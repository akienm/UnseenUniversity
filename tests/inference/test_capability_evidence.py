"""
A model may not claim the TOP capability bucket without measured evidence.

T-inference-cost-first-sort-strands-cloud-fleet.

Why this guard has to exist
---------------------------
The selector sorts by `cost_class_rank` FIRST, and `difficulty_meets` is a `>=` filter. So a
model that claims a bucket also becomes eligible for every bucket beneath it, and among the
eligible it wins on price. **The selector rewards overclaiming.** Nothing checked the claim,
and `difficulty_capable` was hand-typed — the registry had already drifted: `gemini-2.0-flash`
claimed `design` (the old top bucket) while `claude-sonnet-4.6` claimed only `code`.

The damage is worst at the TOP bucket, because that is where escalation lands. A model that
overclaims the top rung makes the last hop of the escalation walk useless: the walk spends up,
gets a model no more capable than the one that just failed, and then raises a capability-ceiling
alarm that names the wrong cause.

Measured, 2026-07-09, before `frontier` existed: across all 450 dimension combinations the
resolver reached exactly 3 of 22 models — deepseek-r1:14b, deepseek-r1:32b, devstral-small-2:24b,
all on `ollama`. The whole cloud fleet, including the paid Ollama-Pro 480b/671b, was structurally
unreachable. Three buckets, and the local box held a member of every one.

Hermetic: reads the registry as data. No device, no provider, no network.
"""

from __future__ import annotations

from unseen_university.devices.inference.connections import default_connections
from unseen_university.devices.inference.models_registry import default_registry as default_models
from unseen_university.devices.inference.routing_buckets import (
    DIFFICULTY_BUCKETS,
    TOP_DIFFICULTY,
    bump_difficulty,
    cost_class_rank,
)
from unseen_university.devices.inference.sources import default_registry as default_sources


def _specs():
    return list(default_models().all())


def test_top_difficulty_is_the_last_bucket():
    assert TOP_DIFFICULTY == DIFFICULTY_BUCKETS[-1]
    assert DIFFICULTY_BUCKETS.index(TOP_DIFFICULTY) == len(DIFFICULTY_BUCKETS) - 1


def test_claiming_the_top_bucket_requires_measured_evidence():
    """THE guard. A cost-first selector rewards overclaiming; the top rung is where it hurts.

    `capability_evidence` must name the measurement. "declared" means a human typed the label,
    which is exactly the unchecked claim this whole ticket is about.
    """
    offenders = [
        f"{s.model_id} (evidence={s.capability_evidence!r})"
        for s in _specs()
        if s.difficulty_capable == TOP_DIFFICULTY
        and not s.capability_evidence.startswith("measured")
    ]
    assert not offenders, (
        f"models claim the top capability bucket {TOP_DIFFICULTY!r} without measured evidence: "
        + "; ".join(offenders)
        + ". Run devlab/claudecode/escalation_matrix.py and record the result, or drop the claim. "
        "A cheap model that overclaims the top bucket captures every bucket beneath it."
    )


def test_the_top_bucket_is_not_empty():
    """If nothing holds the top rung, escalation's last hop resolves to nothing and halts."""
    top = [s.model_id for s in _specs() if s.difficulty_capable == TOP_DIFFICULTY]
    assert top, (
        f"no model claims {TOP_DIFFICULTY!r} — the escalation walk's final hop has no candidate "
        f"and every hard query ends in a capability-ceiling alarm"
    )


def test_the_top_bucket_is_not_held_by_the_local_box():
    """The rung that exists so escalation can LEAVE the local box must not be held by it.

    This is the ticket's core invariant. `difficulty_meets` is `>=` and cost sorts first, so a
    local model at the top bucket would win the top bucket too — and escalation would once again
    never leave `ollama`.
    """
    models, sources = default_models(), default_sources()
    connections = default_connections(models)

    local_at_top = []
    for spec in models.all():
        if spec.difficulty_capable != TOP_DIFFICULTY:
            continue
        for conn in connections.by_model(spec.model_id):
            src = sources.get(conn.source_name)
            if src is not None and cost_class_rank(src.cost_class) <= cost_class_rank("owned_local"):
                local_at_top.append(f"{spec.model_id}@{src.name}")
    assert not local_at_top, (
        "a model on the local box claims the top capability bucket: "
        + ", ".join(local_at_top)
        + ". Cost sorts first, so it wins that bucket, and escalation can never leave the box — "
        "which is the whole defect the bucket was added to fix."
    )


def test_escalation_can_walk_past_the_top_local_rung():
    """Two capability bumps from 'code' must land on a real bucket, not on None.

    Before `frontier` existed, `bump_difficulty('code', 2)` returned None: the walk went
    code -> design -> ceiling alarm. Both of those rungs were local models. There was nowhere
    above the box to go.
    """
    assert bump_difficulty("code", 0) == "code"
    assert bump_difficulty("code", 1) == "design"
    assert bump_difficulty("code", 2) == TOP_DIFFICULTY, (
        "escalating twice from 'code' must reach a capability rung above the local box"
    )
    assert bump_difficulty("code", 3) is None, "and the walk must still terminate"


def test_every_measured_model_names_the_evidence():
    """'measured' without a pointer is just 'declared' wearing a better word."""
    for s in _specs():
        if s.capability_evidence.startswith("measured"):
            assert ":" in s.capability_evidence, (
                f"{s.model_id}: capability_evidence={s.capability_evidence!r} claims measurement "
                f"but names no record — use 'measured:<note-or-matrix-id>'"
            )
