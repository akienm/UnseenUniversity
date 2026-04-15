"""
test_prompt_contexts.py — T-reasoning-prompt-split

Tests for reasoning_context() and voice_context() builders. Both are
pure functions that return structured PromptContext dataclasses.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition.prompt_contexts import (  # noqa: E402
    HYPOTHESIS_DISCLAIMER,
    SIX_RULES_BLOCK,
    VOICE_DISCLAIMER,
    PromptContext,
    Provenance,
    estimate_tokens,
    reasoning_context,
    voice_context,
)


def _prov(caller="test", source="test_situation") -> Provenance:
    return Provenance(caller=caller, situation_source=source)


# ── Provenance dataclass ────────────────────────────────────────────────────


def test_provenance_requires_caller_and_source():
    p = Provenance(caller="test_module", situation_source="cascade_walker")
    assert p.caller == "test_module"
    assert p.situation_source == "cascade_walker"
    assert p.built_at  # auto-populated


# ── reasoning_context: happy path ───────────────────────────────────────────


def test_reasoning_context_minimal_input():
    ctx = reasoning_context(
        situation={"query": "find the goal tree"},
        provenance=_prov(),
    )
    assert ctx.phase == "reasoning"
    assert ctx.system_text
    assert "find the goal tree" in ctx.system_text
    assert "CP1" in ctx.system_text
    assert "CP6" in ctx.system_text
    # Hypothesis disclaimer must be present — CP6 requirement
    assert "HYPOTHESIS" in ctx.system_text.upper()


def test_reasoning_context_includes_six_rules():
    ctx = reasoning_context(situation={"query": "x"}, provenance=_prov())
    assert SIX_RULES_BLOCK in ctx.system_text


def test_reasoning_context_with_milieu():
    ctx = reasoning_context(
        situation={"query": "x"},
        provenance=_prov(),
        milieu={"arousal": 0.7, "valence": 0.2, "notes": "busy day"},
    )
    assert "arousal=0.7" in ctx.system_text
    assert "busy day" in ctx.system_text


def test_reasoning_context_without_milieu_has_baseline():
    ctx = reasoning_context(situation={"query": "x"}, provenance=_prov())
    assert "MILIEU" in ctx.system_text
    assert "baseline" in ctx.system_text


def test_reasoning_context_with_identity():
    ctx = reasoning_context(
        situation={"query": "x"},
        provenance=_prov(),
        identity={
            "name": "Igor",
            "role": "lab partner",
            "traits": ["skeptical", "persistent"],
        },
    )
    assert "lab partner" in ctx.system_text
    assert "skeptical" in ctx.system_text


def test_reasoning_context_with_escalation_trail():
    trail = [
        {
            "level_name": "level_0_exact_recall",
            "status": "exhausted",
            "reason": "empty result",
        },
        {
            "level_name": "level_1_widen_on_miss",
            "status": "exhausted",
            "reason": "no widen hits",
        },
    ]
    ctx = reasoning_context(
        situation={"query": "find tree"},
        provenance=_prov(),
        escalation_trail=trail,
    )
    assert "level_0_exact_recall" in ctx.system_text
    assert "level_1_widen_on_miss" in ctx.system_text


def test_reasoning_context_without_escalation_trail_has_note():
    ctx = reasoning_context(situation={"query": "x"}, provenance=_prov())
    assert "no level trail" in ctx.system_text


def test_reasoning_context_sections_dict_populated():
    ctx = reasoning_context(situation={"query": "x"}, provenance=_prov())
    assert "six_rules" in ctx.sections
    assert "identity" in ctx.sections
    assert "milieu" in ctx.sections
    assert "escalation_trail" in ctx.sections
    assert "situation" in ctx.sections
    assert "disclaimer" in ctx.sections


def test_reasoning_context_disclaimer_matches_constant():
    ctx = reasoning_context(situation={"query": "x"}, provenance=_prov())
    assert ctx.sections["disclaimer"] == HYPOTHESIS_DISCLAIMER


def test_reasoning_context_provenance_preserved():
    p = _prov(caller="tests", source="unit_test")
    ctx = reasoning_context(situation={"query": "x"}, provenance=p)
    assert ctx.provenance is p


# ── reasoning_context: error paths ──────────────────────────────────────────


def test_reasoning_context_requires_provenance():
    with pytest.raises(ValueError, match="provenance is required"):
        reasoning_context(situation={"query": "x"}, provenance=None)  # type: ignore


def test_reasoning_context_refuses_empty_situation():
    with pytest.raises(ValueError, match="'query'"):
        reasoning_context(situation={}, provenance=_prov())


def test_reasoning_context_refuses_none_situation():
    with pytest.raises(ValueError):
        reasoning_context(situation=None, provenance=_prov())  # type: ignore


# ── voice_context: happy path ───────────────────────────────────────────────


def _make_mock_blob(
    intent="inform",
    selected_action="answer directly",
    confidence=0.85,
    hypothesis="the user wants a direct reply",
):
    blob = MagicMock()
    blob.intent = MagicMock()
    blob.intent.value = intent
    blob.selected_action = selected_action
    blob.confidence = confidence
    blob.hypothesis = hypothesis
    return blob


def test_voice_context_minimal_input():
    blob = _make_mock_blob()
    ctx = voice_context(blob, provenance=_prov())
    assert ctx.phase == "voice"
    assert ctx.system_text
    assert "answer directly" in ctx.system_text
    assert "committed output" in ctx.system_text.lower()


def test_voice_context_includes_six_rules():
    blob = _make_mock_blob()
    ctx = voice_context(blob, provenance=_prov())
    assert SIX_RULES_BLOCK in ctx.system_text


def test_voice_context_with_pr_facia():
    blob = _make_mock_blob()
    pr = [
        {
            "id": "PR_AKIEN",
            "display_name": "Akien",
            "relationship_type": "primary",
            "weight": 2.0,
        },
        {
            "id": "PR_IGORS_PROJECT",
            "display_name": "The Igors Project",
            "relationship_type": "project",
            "weight": 1.5,
        },
    ]
    ctx = voice_context(blob, provenance=_prov(), pr_facia=pr)
    assert "PR_AKIEN" in ctx.system_text
    assert "Akien" in ctx.system_text
    assert "PR_IGORS_PROJECT" in ctx.system_text


def test_voice_context_with_recent_ring():
    blob = _make_mock_blob()
    ring = [
        {"category": "user_turn", "content": "hey what's up"},
        {"category": "igor_turn", "content": "working on the cascade"},
    ]
    ctx = voice_context(blob, provenance=_prov(), recent_ring=ring)
    assert "hey what's up" in ctx.system_text
    assert "working on the cascade" in ctx.system_text


def test_voice_context_with_character_hints():
    blob = _make_mock_blob()
    hints = {
        "register": "casual",
        "humor_tolerance": "high",
        "notes": "Akien likes direct biomimetic framing",
    }
    ctx = voice_context(blob, provenance=_prov(), character_hints=hints)
    assert "casual" in ctx.system_text
    assert "high" in ctx.system_text
    assert "biomimetic framing" in ctx.system_text


def test_voice_context_decision_section_present():
    blob = _make_mock_blob(
        selected_action="fetch the goal tree first",
        confidence=0.92,
        hypothesis="the user wants architectural visibility",
    )
    ctx = voice_context(blob, provenance=_prov())
    assert "fetch the goal tree first" in ctx.system_text
    assert "0.92" in ctx.system_text
    assert "architectural visibility" in ctx.system_text


def test_voice_context_sections_dict_populated():
    blob = _make_mock_blob()
    ctx = voice_context(blob, provenance=_prov())
    assert "six_rules" in ctx.sections
    assert "identity" in ctx.sections
    assert "pr_facia" in ctx.sections
    assert "recent_ring" in ctx.sections
    assert "character_hints" in ctx.sections
    assert "decision" in ctx.sections
    assert "disclaimer" in ctx.sections


def test_voice_context_disclaimer_matches_constant():
    blob = _make_mock_blob()
    ctx = voice_context(blob, provenance=_prov())
    assert ctx.sections["disclaimer"] == VOICE_DISCLAIMER


# ── voice_context: error paths ──────────────────────────────────────────────


def test_voice_context_requires_provenance():
    with pytest.raises(ValueError, match="provenance is required"):
        voice_context(_make_mock_blob(), provenance=None)  # type: ignore


def test_voice_context_refuses_none_blob():
    with pytest.raises(ValueError, match="DecisionBlob"):
        voice_context(None, provenance=_prov())  # type: ignore


# ── Optional-field fallbacks (CP1: don't fake certainty) ───────────────────


def test_reasoning_context_without_identity_uses_default():
    ctx = reasoning_context(situation={"query": "x"}, provenance=_prov())
    assert "IDENTITY" in ctx.system_text
    assert "Igor" in ctx.system_text


def test_voice_context_without_pr_facia_has_note():
    blob = _make_mock_blob()
    ctx = voice_context(blob, provenance=_prov())
    assert "PR FACIA" in ctx.system_text
    assert "no persistent-relationship" in ctx.system_text


def test_voice_context_without_recent_ring_has_note():
    blob = _make_mock_blob()
    ctx = voice_context(blob, provenance=_prov())
    assert "RECENT RING" in ctx.system_text
    assert "no recent conversation" in ctx.system_text


def test_voice_context_without_character_hints_has_defaults():
    blob = _make_mock_blob()
    ctx = voice_context(blob, provenance=_prov())
    assert "CHARACTER HINTS" in ctx.system_text


# ── Token estimate ──────────────────────────────────────────────────────────


def test_estimate_tokens_returns_positive_integer():
    ctx = reasoning_context(situation={"query": "x"}, provenance=_prov())
    n = estimate_tokens(ctx)
    assert isinstance(n, int)
    assert n > 0


def test_voice_context_larger_than_reasoning_when_loaded():
    """Voice context with full PR facia + ring + hints should be larger
    than reasoning context with the same query — that's the whole point
    of the split."""
    reasoning = reasoning_context(situation={"query": "x"}, provenance=_prov())

    blob = _make_mock_blob()
    pr = [
        {
            "id": f"PR_{i}",
            "display_name": f"entity_{i}",
            "relationship_type": "x",
            "weight": 1.0,
        }
        for i in range(5)
    ]
    ring = [{"category": "turn", "content": "x" * 150} for _ in range(6)]
    hints = {"register": "casual", "humor_tolerance": "mild", "notes": "y" * 200}
    voice = voice_context(
        blob,
        provenance=_prov(),
        pr_facia=pr,
        recent_ring=ring,
        character_hints=hints,
    )
    assert estimate_tokens(voice) > estimate_tokens(reasoning)


# ── Determinism (same inputs → same output) ────────────────────────────────


def test_reasoning_context_deterministic_modulo_provenance_timestamp():
    """Same inputs should produce the same system_text (provenance
    timestamps may differ)."""
    situation = {"query": "find X"}
    milieu = {"arousal": 0.5, "valence": 0.0}
    a = reasoning_context(situation=situation, provenance=_prov(), milieu=milieu)
    b = reasoning_context(situation=situation, provenance=_prov(), milieu=milieu)
    assert a.system_text == b.system_text


# ── Phase tagging ───────────────────────────────────────────────────────────


def test_phases_are_distinct():
    r = reasoning_context(situation={"query": "x"}, provenance=_prov())
    v = voice_context(_make_mock_blob(), provenance=_prov())
    assert r.phase != v.phase
    assert r.phase == "reasoning"
    assert v.phase == "voice"


# ── PromptContext convenience methods ───────────────────────────────────────


def test_prompt_context_to_string():
    ctx = reasoning_context(situation={"query": "x"}, provenance=_prov())
    s = ctx.to_string()
    assert s == ctx.system_text


def test_prompt_context_to_sections_returns_copy():
    ctx = reasoning_context(situation={"query": "x"}, provenance=_prov())
    d = ctx.to_sections()
    # Should be a copy — mutating d shouldn't affect ctx.sections
    d["injected"] = "bad"
    assert "injected" not in ctx.sections
