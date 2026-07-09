"""
A model may not claim a capability bucket beyond what was MEASURED, at a budget that did not bind.

T-inference-cost-first-sort-strands-cloud-fleet (the top-bucket guard) and
T-capability-measured-at-a-budget-ceiling (the conditions guard).

Why these guards have to exist
------------------------------
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

And why "measured" alone was not enough
---------------------------------------
Writing the word `measured` on a hand-typed label just moves the wish one field over. A
measurement is only true for the CONDITIONS it ran under, and the condition that matters most
here is the token budget: a reasoning model spends its budget inside `<think>` before it writes
a word of answer, so at a tight budget it truncates and reads as incapable. That is not
hypothetical — `gemini-2.5-flash` was scored LESS CAPABLE than a local 32b from replies cut off
mid-derivation, and `deepseek-r1:14b` passed a frontier query at 8192 tokens that
`deepseek-r1:32b` "failed" at 4096.

So `test_a_measured_capability_claim_is_backed_by_its_measurement` does not check the SHAPE of
the evidence. It resolves the evidence's `record` against the memory store, cross-checks the
conditions the registry claims against the conditions the sweep actually ran under, and rejects
any claim for a model that TRUNCATED at that ceiling — because a run whose budget bound cannot
tell "cannot" from "ran out of room". A format check would have been green on hand-typed numbers,
which is the same hollow build this ticket exists to close.

Hermetic: reads the registry and the committed note as data. No device, no provider, no network.
"""

from __future__ import annotations

import json
from pathlib import Path

from unseen_university._uu_root import uu_root
from unseen_university.devices.inference.capability_evidence import MIN_SAMPLES
from unseen_university.devices.inference.connections import default_connections
from unseen_university.devices.inference.domains.escalation_corpus import BANDS
from unseen_university.devices.inference.models_registry import default_registry as default_models
from unseen_university.devices.inference.routing_buckets import (
    DIFFICULTY_BUCKETS,
    TOP_DIFFICULTY,
    bump_difficulty,
    cost_class_rank,
)
from unseen_university.devices.inference.sources import default_registry as default_sources

#: The corpus band whose reasoning SHAPE defines the top capability bucket. Claiming
#: TOP_DIFFICULTY means clearing this band cleanly — that is the link between the label the
#: router reads and the thing the instrument actually measured.
TOP_BAND = BANDS[-1]


def _specs():
    return list(default_models().all())


def _measured_specs():
    return [s for s in _specs() if s.capability_evidence.is_measured]


def _load_note(record: str) -> dict:
    """Resolve an evidence `record` (a memory-store note namespace) to the note body.

    The note filename carries a microsecond timestamp, so the pointer names the NAMESPACE and we
    take the most recent note under it. That is deliberate: if a later sweep runs at a different
    ceiling, the conditions cross-check below fails loudly instead of leaving a stale registry
    claim standing behind fresh data.
    """
    notes = Path(uu_root()) / "devlab" / "runtime" / "memory" / "notes"
    hits = sorted(notes.glob(f"*{record}*.json"))
    assert hits, (
        f"capability evidence points at record {record!r} but no note matches "
        f"{notes}/*{record}*.json — the claim names a measurement that does not exist. "
        f"Run devlab/claudecode/escalation_matrix.py --emit-note."
    )
    return json.loads(hits[-1].read_text())["body"]


def test_top_difficulty_is_the_last_bucket():
    assert TOP_DIFFICULTY == DIFFICULTY_BUCKETS[-1]
    assert DIFFICULTY_BUCKETS.index(TOP_DIFFICULTY) == len(DIFFICULTY_BUCKETS) - 1


def test_claiming_the_top_bucket_requires_measured_evidence():
    """THE guard. A cost-first selector rewards overclaiming; the top rung is where it hurts.

    `capability_evidence` must name the measurement. DECLARED means a human typed the label,
    which is exactly the unchecked claim this whole ticket is about.
    """
    offenders = [
        f"{s.model_id} (evidence={s.capability_evidence})"
        for s in _specs()
        if s.difficulty_capable == TOP_DIFFICULTY and not s.capability_evidence.is_measured
    ]
    assert not offenders, (
        f"models claim the top capability bucket {TOP_DIFFICULTY!r} without measured evidence: "
        + "; ".join(offenders)
        + ". Run devlab/claudecode/escalation_matrix.py and record the result, or drop the claim. "
        "A cheap model that overclaims the top bucket captures every bucket beneath it."
    )


def test_a_measured_capability_claim_is_backed_by_its_measurement():
    """THE conditions guard — and the proof node for T-capability-measured-at-a-budget-ceiling.

    Three things, all of which must hold, and none of which a hand-typed number can satisfy:

    1. The evidence NAMES its conditions — the ceiling budget and the sample count. A capability
       verdict without the budget it was measured at is not a fact about the model.
    2. Those conditions MATCH the note the evidence points at. You cannot write a ceiling into
       the registry that no sweep ever ran.
    3. The model did not TRUNCATE at that ceiling. If it did, the budget bound, and every
       verdict in that run is ambiguous between "cannot" and "ran out of room" — so no capability
       claim rests on it, however good the pass-rate looked.
    """
    for spec in _measured_specs():
        ev = spec.capability_evidence
        assert ev.record, f"{spec.model_id}: evidence claims measurement but names no record"
        assert ev.ceiling_tokens > 0, (
            f"{spec.model_id}: capability_evidence={ev} claims a measurement but does not name "
            f"the token CEILING it was measured at. A wrong answer at an unnamed budget is "
            f"ambiguous between 'cannot' and 'ran out of room' — it licenses no capability claim."
        )
        assert ev.samples >= MIN_SAMPLES, (
            f"{spec.model_id}: capability_evidence={ev} rests on {ev.samples} sample(s). "
            f"One sample at temperature 0 is one sample, not a property — need >= {MIN_SAMPLES}."
        )

        note = _load_note(ev.record)
        conditions = note["conditions"]
        assert ev.ceiling_tokens == conditions["ceiling_tokens"], (
            f"{spec.model_id}: registry claims a ceiling of {ev.ceiling_tokens} tokens but note "
            f"{ev.record!r} was measured at {conditions['ceiling_tokens']}. The claim does not "
            f"rest on the measurement it points at."
        )
        assert ev.samples == conditions["samples_per_cell"], (
            f"{spec.model_id}: registry claims {ev.samples} samples/cell but note {ev.record!r} "
            f"ran {conditions['samples_per_cell']}."
        )

        assert spec.model_id in note["models"], (
            f"{spec.model_id}: evidence points at note {ev.record!r}, which never measured it"
        )
        row = note["models"][spec.model_id]
        truncations = row["truncations"]
        assert truncations == 0, (
            f"{spec.model_id}: {truncations} cell(s) truncated at the {ev.ceiling_tokens}-token "
            f"ceiling, so the ceiling BOUND. No capability verdict is licensed from that run — "
            f"raise the ceiling and re-measure, or drop the claim to `declared`."
        )
        # An instrument error is not a wrong answer — and it is not a verdict either. Measured
        # 2026-07-09: deepseek-v3.1:671b-cloud solves b5-frobenius twice at ceiling=4096 and TIMES
        # OUT twice at ceiling=32768. The ceiling CAUSED the error. So an errored cell voids a
        # claim for exactly the reason a truncated one does: that cell has no result, and the
        # thing that took it away was the condition we are claiming the result under.
        errors = row["errors"]
        assert errors == 0, (
            f"{spec.model_id}: {errors} cell(s) errored at the {ev.ceiling_tokens}-token ceiling. "
            f"An error is not a wrong answer and it is not a verdict — and a ceiling-induced "
            f"timeout is caused by the very condition the claim rests on. Re-measure with a "
            f"timeout that does not bind, or drop the claim to `declared`."
        )


def test_the_top_bucket_claim_is_backed_by_a_clean_pass_of_the_top_band():
    """Claiming the top bucket means clearing the top BAND — every query, every sample.

    This is the join between the label the router reads and the thing the instrument measured.
    Without it, `difficulty_capable='frontier'` and the corpus are two unrelated facts, and the
    registry could name any model as the escalation walk's last hope.
    """
    for spec in _measured_specs():
        if spec.difficulty_capable != TOP_DIFFICULTY:
            continue
        note = _load_note(spec.capability_evidence.record)
        band = note["models"][spec.model_id]["bands"][TOP_BAND]
        assert band["pass"] == band["total"] and band["total"] > 0, (
            f"{spec.model_id} claims {TOP_DIFFICULTY!r} but scored {band['pass']}/{band['total']} "
            f"on {TOP_BAND} (fail={band['fail']}, truncated={band['truncated']}, "
            f"unstable={band['unstable']}). The escalation walk's final hop would land on a model "
            f"that cannot solve what the rung below it could not."
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


def test_the_registry_actually_claims_some_measured_capability():
    """Guard against a vacuous pass: every loop above iterates the measured specs.

    An empty registry, or one where every claim quietly reverted to `declared`, would make the
    conditions guard pass by never executing its body — the same shape of hollow green the
    corpus tests grew a `assert CORPUS` guard for.
    """
    assert _measured_specs(), (
        "no model in the registry carries measured capability evidence — the conditions guard "
        "above would pass vacuously"
    )
