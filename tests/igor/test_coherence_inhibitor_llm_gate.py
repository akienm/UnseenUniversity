"""tests/test_coherence_inhibitor_llm_gate.py

Tests the D-web-reply-coherence-inhibitor-fix-2026-04-23 fix:
suppress_incoherent() must only suppress HABIT-sourced responses,
never LLM-tier responses — Jaccard overlap is wrong metric for
conversational replies.

Real-world cases verified:
- 01cb0591 (score=0.070, source=llm_or_tier0): was incorrectly suppressed,
  now passes through
- dc50fdc5 (score=0.099, source=llm_or_tier0): was incorrectly suppressed,
  now passes through
- Habit misfire (score<threshold, source=habit:X): still suppressed correctly
"""

import pytest

from devices.igor.cognition.response_coherence_inhibitor import suppress_incoherent


def _flagged(score: float) -> dict:
    return {"flagged": True, "score": score}


def _unflagged(score: float) -> dict:
    return {"flagged": False, "score": score}


RESPONSE = "This is a substantive LLM reply that introduces new concepts."


# ── LLM-tier responses: never suppressed ────────────────────────────────────


class TestLlmSourceNotSuppressed:
    def test_llm_or_tier0_flagged_not_suppressed(self):
        result = suppress_incoherent(_flagged(0.070), RESPONSE, "llm_or_tier0")
        assert result == RESPONSE

    def test_llm_or_tier0_flagged_dc50_score_not_suppressed(self):
        """Real case: dc50fdc5 had score=0.099, source=llm_or_tier0."""
        result = suppress_incoherent(_flagged(0.099), RESPONSE, "llm_or_tier0")
        assert result == RESPONSE

    def test_empty_source_flagged_not_suppressed(self):
        """Default source_label='' is not habit — should not suppress."""
        result = suppress_incoherent(_flagged(0.05), RESPONSE)
        assert result == RESPONSE

    def test_empty_source_flagged_returns_response_unchanged(self):
        result = suppress_incoherent(_flagged(0.00), "Some reply.", "")
        assert result == "Some reply."


# ── Habit sources: still suppressed ─────────────────────────────────────────


class TestHabitSourceSuppressed:
    def test_habit_source_flagged_returns_empty(self):
        result = suppress_incoherent(_flagged(0.07), RESPONSE, "habit:SOME_HABIT_ID")
        assert result == ""

    def test_habit_source_any_id_suppressed(self):
        result = suppress_incoherent(_flagged(0.05), RESPONSE, "habit:PROC_GREET_AKIEN")
        assert result == ""

    def test_habit_source_borderline_score_suppressed(self):
        """Score 0.09 (below 0.10 threshold) with habit source → suppressed."""
        result = suppress_incoherent(
            _flagged(0.09), RESPONSE, "habit:WINNOW_B627137784"
        )
        assert result == ""


# ── Unflagged responses: always pass through ────────────────────────────────


class TestUnflaggedAlwaysPassThrough:
    def test_unflagged_llm_passes_through(self):
        result = suppress_incoherent(_unflagged(0.5), RESPONSE, "llm_or_tier0")
        assert result == RESPONSE

    def test_unflagged_habit_passes_through(self):
        """Even habit source: if not flagged, don't suppress."""
        result = suppress_incoherent(_unflagged(0.5), RESPONSE, "habit:X")
        assert result == RESPONSE

    def test_unflagged_empty_source_passes_through(self):
        result = suppress_incoherent(_unflagged(0.0), RESPONSE)
        assert result == RESPONSE


# ── Source label prefix matching ─────────────────────────────────────────────


class TestSourceLabelPrefixMatching:
    def test_habit_prefix_required_not_just_containing(self):
        """'not_a_habit:X' should NOT be treated as a habit source."""
        result = suppress_incoherent(_flagged(0.05), RESPONSE, "not_a_habit:X")
        assert result == RESPONSE

    def test_habit_prefix_case_sensitive(self):
        """'Habit:X' (uppercase H) is not a habit source — case-sensitive."""
        result = suppress_incoherent(_flagged(0.05), RESPONSE, "Habit:X")
        assert result == RESPONSE


# ── T-coherence-inhibitor-winnow-exemption: llm_via_ labels ──────────────────


class TestLlmViaLabel:
    """T-coherence-inhibitor-winnow-exemption (2026-04-24):
    main.py now labels non-PROCEDURAL BG winners (WINNOW_xxx INTERPRETIVE
    hints) as 'llm_via_<id>' instead of 'habit:<id>', because those winners
    drive LLM dispatch rather than emitting a templated response. The
    suppression rule must exempt that prefix so real conversational LLM
    replies aren't silenced by low Jaccard overlap.
    """

    def test_llm_via_winnow_not_suppressed(self):
        """Today's failing case: WINNOW_B627137784 dispatched a tier.4
        LLM reply ('What's the thing you've been carrying around in your
        head lately...') that scored 0.000 Jaccard vs the short prompt —
        coherent conversational answer, correctly not suppressed once
        main.py labels the source as llm_via_ instead of habit:."""
        result = suppress_incoherent(
            _flagged(0.000),
            "What's the thing you've been carrying around in your head lately?",
            "llm_via_WINNOW_B627137784",
        )
        assert (
            result
            == "What's the thing you've been carrying around in your head lately?"
        )

    def test_llm_via_any_id_not_suppressed(self):
        result = suppress_incoherent(_flagged(0.05), RESPONSE, "llm_via_SOMETHING")
        assert result == RESPONSE

    def test_main_py_labels_non_procedural_as_llm_via(self):
        """Regression guard on main.py source: the coherence-inhibitor
        source_label construction must only call something 'habit:' when
        the winning BG entry is PROCEDURAL. INTERPRETIVE winners (e.g.
        WINNOW_xxx hints) must use the 'llm_via_' prefix.
        """
        from pathlib import Path

        src = (
            Path(__file__).resolve().parent.parent.parent / "devices/igor/main.py"
        ).read_text()
        assert 'f"llm_via_{_turn_habit.id}"' in src, (
            "main.py coherence source_label must emit 'llm_via_<id>' for "
            "non-PROCEDURAL BG winners — T-coherence-inhibitor-winnow-exemption"
        )
        assert '_mt_str == "PROCEDURAL"' in src, (
            "source_label branch must gate the habit: label on the memory_type "
            "being PROCEDURAL — not on _turn_habit being truthy alone"
        )


# ── Empty / edge cases ───────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_response_with_habit_suppression(self):
        result = suppress_incoherent(_flagged(0.05), "", "habit:X")
        assert result == ""

    def test_empty_response_with_llm_source(self):
        result = suppress_incoherent(_flagged(0.05), "", "llm_or_tier0")
        assert result == ""

    def test_none_flagged_key_treated_as_unflagged(self):
        """result without 'flagged' key → not suppressed."""
        result = suppress_incoherent({}, RESPONSE, "habit:X")
        assert result == RESPONSE
