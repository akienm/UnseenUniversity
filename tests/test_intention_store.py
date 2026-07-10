"""Contract tests for the INTENTION artifact — T-intention-capture-deconstruct-skill.

The intention is the front-boundary artifact of the INTENTION -> DESIGN -> TICKET
stack: a present-tense contract that DECONSTRUCTS into sub-intentions, each carrying
its proof-obligation (proof-as-thread). These tests pin the two contract tiers and
the non-evadable write gate.

PROOF NODE (proof-on-close, red->green a hollow build can't pass):
    test_subintention_missing_proof_obligation_rejected
A deconstruction whose sub-intention carries no proof-obligation is the hollow shape
this artifact exists to reject. Stub (commit A) does not enforce it, so the node
fails with "DID NOT RAISE"; the enforced contract (commit B) rejects it (green).
"""

import json
from pathlib import Path

import pytest

from unseen_university import intention_store as ins
from devlab.claudecode import intention_emit


@pytest.fixture(autouse=True)
def _tmp_root(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    yield tmp_path


def _base_intention(**overrides) -> dict:
    """A captured (base-tier) intention — matches the existing flat I-* records."""
    body = {
        "intention_id": "I-sample",
        "statement": "I intend that the sample subsystem does X observably.",
        "status": "active",
        "date": "2026-07-10",
    }
    body.update(overrides)
    return body


def _deconstructed(**overrides) -> dict:
    """A fully deconstructed intention satisfying the full tier."""
    body = _base_intention(
        why="X is the load-bearing behaviour the subsystem exists for.",
        how_to_verify="run the sample flow and observe X in the output.",
        constraints=["no sqlite", "canonical memory home"],
        sub_intentions=[
            {
                "statement": "I intend that the store validates before write.",
                "why": "a soft caller-side gate is attacker-controlled.",
                "proof_obligation": "emit(hollow) raises before any file is written.",
            },
            {
                "statement": "I intend that a base-tier record still validates.",
                "why": "existing flat records must not be retroactively broken.",
                "proof_obligation": "validate_intention(flat) does not raise.",
            },
        ],
    )
    body.update(overrides)
    return body


# ── PROOF NODE ─────────────────────────────────────────────────────────────────
def test_subintention_missing_proof_obligation_rejected():
    """A sub-intention with no proof-obligation breaks the proof thread — reject it."""
    bad = _deconstructed(sub_intentions=[{
        "statement": "I intend that X.",
        "why": "because.",
        # no proof_obligation
    }])
    with pytest.raises(ins.IntentionValidationError):
        ins.validate_intention(bad, deconstructed=True)


def test_deconstructed_with_no_subintentions_rejected():
    """A deconstruction that produced no sub-intentions is not a deconstruction."""
    with pytest.raises(ins.IntentionValidationError):
        ins.validate_intention(_deconstructed(sub_intentions=[]), deconstructed=True)


def test_complete_deconstruction_accepted():
    """The full, honest deconstruction passes."""
    ins.validate_intention(_deconstructed(), deconstructed=True)  # must not raise


def test_base_tier_backward_compatible():
    """A flat captured intention (existing I-* shape) validates at the base tier —
    nothing retroactively broken."""
    ins.validate_intention(_base_intention())  # deconstructed=False default


def test_missing_statement_rejected():
    """An intention with no present-tense statement is rejected at every tier."""
    with pytest.raises(ins.IntentionValidationError):
        ins.validate_intention(_base_intention(statement=""))


def test_emit_validates_before_write():
    """The write gate is enforced in code: a hollow deconstruction never lands."""
    with pytest.raises(ins.IntentionValidationError):
        intention_emit.emit_intention(
            _deconstructed(sub_intentions=[{"statement": "x", "why": "y"}]),
            deconstructed=True)
    assert ins.get_intention("I-sample") is None


def test_deconstruction_roundtrips_proof_obligations():
    """emit -> read back: each sub-intention's proof-obligation is retrievable
    (not just an envelope). Envelope != work."""
    path = intention_emit.emit_intention(_deconstructed(), deconstructed=True)
    assert Path(path).exists()
    rec = ins.get_intention("I-sample")
    assert rec is not None
    subs = rec["body"]["sub_intentions"]
    assert len(subs) >= 2
    assert all(s["proof_obligation"].strip() for s in subs)
