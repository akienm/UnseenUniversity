"""
test_decision_blob.py — T-decision-blob-schema

Unit tests for the decision blob schema. The schema is the contract
between substrate, reasoning, experiment, and voice — tests verify:

  1. Construction + field validation (confidence range, provenance required)
  2. blob_id format matches D256 / T-architecture-core-principles rule
  3. CP-validation flags work as intended (CP1/CP3 block commitment)
  4. can_commit() surfaces all blocking reasons
  5. Serialization roundtrip (dict, JSON)
  6. Hypothesis-without-experiment is blocked by CP6
  7. Low confidence blocks commitment per CP1
  8. from_substrate() helper produces valid blobs
  9. Enum coercion works (string intent → Intent enum)
  10. Alternatives + importance weights survive roundtrip
"""

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition.decision_blob import (  # noqa: E402
    Alternative,
    CPValidation,
    DecisionBlob,
    Intent,
    ProposedExperiment,
    Provenance,
    from_substrate,
)

# ── Construction + validation ────────────────────────────────────────────────


def _make_provenance() -> Provenance:
    return Provenance(maker="substrate", inputs=["test"])


def test_minimum_construction():
    blob = DecisionBlob(intent=Intent.OBSERVE, provenance=_make_provenance())
    assert blob.intent == Intent.OBSERVE
    assert blob.confidence == 0.0
    assert blob.selected_action is None
    assert blob.hypothesis is None
    assert blob.blob_id  # auto-generated


def test_requires_provenance():
    with pytest.raises(ValueError, match="CP6 demands verifiability"):
        DecisionBlob(intent=Intent.OBSERVE, provenance=None)


def test_confidence_range_validation():
    with pytest.raises(ValueError, match="confidence must be in"):
        DecisionBlob(
            intent=Intent.ANSWER, confidence=1.5, provenance=_make_provenance()
        )
    with pytest.raises(ValueError, match="confidence must be in"):
        DecisionBlob(
            intent=Intent.ANSWER, confidence=-0.1, provenance=_make_provenance()
        )


def test_intent_coercion_from_string():
    blob = DecisionBlob(intent="answer", provenance=_make_provenance())  # type: ignore
    assert blob.intent == Intent.ANSWER
    assert isinstance(blob.intent, Intent)


# ── blob_id format ───────────────────────────────────────────────────────────


def test_blob_id_format_matches_d256():
    """Format: yyyymmdd.hhmmssuuuuuu.xxxxxxx (date . time-with-microseconds . short-tag)."""
    blob = DecisionBlob(intent=Intent.OBSERVE, provenance=_make_provenance())
    # 8 digits . 12 digits . 7 hex chars
    assert re.match(r"^\d{8}\.\d{12}\.[a-f0-9]{7}$", blob.blob_id)


def test_blob_ids_are_unique_within_microsecond():
    # Generate several blobs fast — the hex tag should disambiguate
    ids = {
        DecisionBlob(intent=Intent.OBSERVE, provenance=_make_provenance()).blob_id
        for _ in range(100)
    }
    assert len(ids) == 100


# ── CP validation ────────────────────────────────────────────────────────────


def test_cp_validation_incomplete_without_why():
    cp = CPValidation()
    assert not cp.is_complete()
    cp.cp3_has_why = "user asked"
    assert cp.is_complete()


def test_cp_validation_blocks_commitment_without_why():
    cp = CPValidation()
    blocks = cp.blocks_commitment()
    assert any("CP3" in b for b in blocks)


def test_cp_validation_blocks_commitment_when_not_provisional():
    cp = CPValidation(cp1_provisional=False, cp3_has_why="because")
    blocks = cp.blocks_commitment()
    assert any("CP1" in b for b in blocks)


def test_cp_validation_clean_when_compliant():
    cp = CPValidation(cp1_provisional=True, cp3_has_why="substrate decided")
    assert cp.blocks_commitment() == []


# ── can_commit() ─────────────────────────────────────────────────────────────


def test_can_commit_requires_selected_action():
    blob = DecisionBlob(
        intent=Intent.ANSWER,
        confidence=0.9,
        cp_validation=CPValidation(cp3_has_why="tested"),
        provenance=_make_provenance(),
    )
    ok, reasons = blob.can_commit()
    assert not ok
    assert any("selected_action is None" in r for r in reasons)


def test_can_commit_hypothesis_without_action_blocks():
    """CP6: hypothesis from LLM must be tested before becoming selected_action."""
    blob = DecisionBlob(
        intent=Intent.ANSWER,
        hypothesis="maybe X causes Y",
        selected_action=None,
        confidence=0.8,
        cp_validation=CPValidation(cp3_has_why="llm said so"),
        provenance=_make_provenance(),
    )
    ok, reasons = blob.can_commit()
    assert not ok
    assert any("hypothesis present" in r for r in reasons)


def test_can_commit_low_confidence_blocks():
    """CP1: low confidence should not commit; experiment or defer."""
    blob = DecisionBlob(
        intent=Intent.ANSWER,
        selected_action="do X",
        confidence=0.3,
        cp_validation=CPValidation(cp3_has_why="weak signal"),
        provenance=_make_provenance(),
    )
    ok, reasons = blob.can_commit()
    assert not ok
    assert any("confidence" in r.lower() for r in reasons)


def test_can_commit_happy_path():
    blob = DecisionBlob(
        intent=Intent.ANSWER,
        selected_action="respond: hello",
        confidence=0.85,
        cp_validation=CPValidation(cp3_has_why="strong habit match"),
        provenance=_make_provenance(),
    )
    ok, reasons = blob.can_commit()
    assert ok, f"expected clean commit, got reasons: {reasons}"
    assert reasons == []


# ── Serialization ────────────────────────────────────────────────────────────


def test_to_dict_roundtrip():
    blob = DecisionBlob(
        intent=Intent.ANSWER,
        selected_action="reply",
        confidence=0.9,
        considered_alternatives=[
            Alternative(candidate="A", score=0.7, reason="close second"),
            Alternative(candidate="B", score=0.6, reason="dropped"),
        ],
        importance_weights={"urgency": 0.8, "milieu": 0.3},
        cp_validation=CPValidation(cp3_has_why="bg winner"),
        provenance=_make_provenance(),
    )
    d = blob.to_dict()
    assert d["intent"] == "answer"  # enum serialized as value
    back = DecisionBlob.from_dict(d)
    assert back.intent == Intent.ANSWER
    assert back.selected_action == "reply"
    assert len(back.considered_alternatives) == 2
    assert back.considered_alternatives[0].candidate == "A"
    assert back.importance_weights["urgency"] == 0.8


def test_to_json_roundtrip():
    blob = DecisionBlob(
        intent=Intent.EXPERIMENT,
        hypothesis="X triggers Y",
        proposed_experiment=ProposedExperiment(
            hypothesis="X triggers Y",
            probe="call tool Z with input X",
            expected_observation="Y in output",
        ),
        cp_validation=CPValidation(cp3_has_why="substrate declined to commit"),
        provenance=_make_provenance(),
    )
    text = blob.to_json()
    back = DecisionBlob.from_json(text)
    assert back.intent == Intent.EXPERIMENT
    assert back.hypothesis == "X triggers Y"
    assert back.proposed_experiment is not None
    assert back.proposed_experiment.probe == "call tool Z with input X"


def test_provenance_survives_roundtrip():
    blob = DecisionBlob(
        intent=Intent.OBSERVE,
        provenance=Provenance(
            maker="substrate",
            inputs=["user_input", "twm_state"],
            thread_id="t-abc",
            turn_id="turn-123",
        ),
    )
    back = DecisionBlob.from_dict(blob.to_dict())
    assert back.provenance.maker == "substrate"
    assert back.provenance.thread_id == "t-abc"
    assert back.provenance.turn_id == "turn-123"
    assert "twm_state" in back.provenance.inputs


# ── from_substrate helper ────────────────────────────────────────────────────


def test_from_substrate_produces_valid_blob():
    blob = from_substrate(
        intent=Intent.OBSERVE,
        considered=[Alternative(candidate="greet", score=0.4)],
        weights={"greeting_match": 0.4},
        confidence=0.4,
        thread_id="t-xyz",
        turn_id="turn-456",
        why="substrate ran BG selection, no confident winner",
        verified_sources=["cortex.search"],
        trail_id="trail-789",
    )
    assert blob.intent == Intent.OBSERVE
    assert blob.provenance.maker == "substrate"
    assert blob.provenance.thread_id == "t-xyz"
    assert (
        blob.cp_validation.cp3_has_why
        == "substrate ran BG selection, no confident winner"
    )
    assert "cortex.search" in blob.cp_validation.cp6_sources_verified
    assert blob.trail_id == "trail-789"


def test_from_substrate_low_confidence_correctly_blocks_commit():
    """Integration: substrate produces a low-confidence blob → cannot commit."""
    blob = from_substrate(
        intent=Intent.ANSWER,
        confidence=0.2,
        why="uncertain — no strong habit match",
    )
    ok, reasons = blob.can_commit()
    assert not ok
    # Should block on both no-selected-action AND low-confidence
    assert any("confidence" in r.lower() for r in reasons)


def test_from_substrate_intent_string_coercion():
    blob = from_substrate(intent="answer", why="testing")
    assert blob.intent == Intent.ANSWER


def test_from_substrate_defaults():
    blob = from_substrate(intent=Intent.OBSERVE, why="minimal")
    assert blob.considered_alternatives == []
    assert blob.importance_weights == {}
    assert blob.confidence == 0.0
    assert blob.cp_validation.cp6_sources_verified == []
    assert blob.trail_id is None
