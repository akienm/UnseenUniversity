"""T-tutor-not-oracle-prompt: reasoning_context emits tutor-mode prompt
that asks upstream LLM for thinking-frame, not direct answer.

Tutor mode is the default. Answer mode is the opt-in for
translation/summarization calls that actually want direct output.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.cognition.prompt_contexts import (
    reasoning_context,
    Provenance,
    TUTOR_DIRECTIVE,
    HYPOTHESIS_DISCLAIMER,
)


def _min_situation():
    return {"query": "how should Igor handle ambiguous stimuli at low arousal?"}


def _min_provenance():
    return Provenance(caller="test", situation_source="test")


def test_default_mode_is_tutor():
    """Absent explicit mode, reasoning_context produces tutor-mode prompt."""
    ctx = reasoning_context(_min_situation(), provenance=_min_provenance())
    assert TUTOR_DIRECTIVE in ctx.system_text
    assert "tutor" in ctx.system_text.lower()


def test_explicit_tutor_mode_includes_directive():
    """mode='tutor' explicitly includes TUTOR_DIRECTIVE."""
    ctx = reasoning_context(
        _min_situation(), provenance=_min_provenance(), mode="tutor"
    )
    assert TUTOR_DIRECTIVE in ctx.system_text


def test_answer_mode_excludes_directive():
    """mode='answer' skips TUTOR_DIRECTIVE so the LLM can produce direct output."""
    ctx = reasoning_context(
        _min_situation(), provenance=_min_provenance(), mode="answer"
    )
    assert TUTOR_DIRECTIVE not in ctx.system_text
    # Hypothesis disclaimer still present — CP6 always held regardless of mode
    assert HYPOTHESIS_DISCLAIMER in ctx.system_text


def test_unknown_mode_defaults_to_tutor():
    """Unknown / misspelled mode → tutor. Default is strict to prevent
    accidental answer-mode when caller passes 'Tutor' or misspells."""
    ctx = reasoning_context(
        _min_situation(), provenance=_min_provenance(), mode="teacher"
    )
    assert TUTOR_DIRECTIVE in ctx.system_text

    ctx_empty = reasoning_context(
        _min_situation(), provenance=_min_provenance(), mode=""
    )
    assert TUTOR_DIRECTIVE in ctx_empty.system_text


def test_tutor_directive_content_shape():
    """TUTOR_DIRECTIVE must instruct the LLM to ask questions and surface
    options rather than give answers. If someone edits the directive to
    reintroduce answer-shape, this test surfaces it."""
    # Ask-questions shape
    assert "question" in TUTOR_DIRECTIVE.lower()
    # Options-not-answers shape
    assert "option" in TUTOR_DIRECTIVE.lower()
    # Explicit anti-oracle framing
    assert "tutor" in TUTOR_DIRECTIVE.lower()
    assert "not" in TUTOR_DIRECTIVE.lower()
    # Should warn against direct-answer shape
    assert (
        "solution is" in TUTOR_DIRECTIVE.lower()
        or "direct answer" in TUTOR_DIRECTIVE.lower()
        or "solve it for him" in TUTOR_DIRECTIVE.lower()
    )


def test_tutor_directive_does_not_leak_into_answer_mode():
    """Answer mode must strip the directive completely, not just part of it."""
    ctx = reasoning_context(
        _min_situation(), provenance=_min_provenance(), mode="answer"
    )
    # None of the distinctive tutor phrases should appear
    assert "TUTOR MODE" not in ctx.system_text
    assert "reasoning tutor" not in ctx.system_text
    assert "think, do not think for him" not in ctx.system_text


def test_sections_dict_records_mode_choice():
    """sections['tutor_directive'] should be TUTOR_DIRECTIVE in tutor mode
    and empty string in answer mode, so callers can introspect."""
    tutor_ctx = reasoning_context(
        _min_situation(), provenance=_min_provenance(), mode="tutor"
    )
    answer_ctx = reasoning_context(
        _min_situation(), provenance=_min_provenance(), mode="answer"
    )
    assert tutor_ctx.sections["tutor_directive"] == TUTOR_DIRECTIVE
    assert answer_ctx.sections["tutor_directive"] == ""


def test_provenance_still_required():
    """Tutor mode doesn't relax CP3 — provenance still required."""
    import pytest

    with pytest.raises(ValueError, match="provenance"):
        reasoning_context(_min_situation(), provenance=None, mode="tutor")  # type: ignore
