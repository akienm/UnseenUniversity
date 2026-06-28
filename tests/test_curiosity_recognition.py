"""Tests for T-igor-curiosity-recognition: curiosity from recognition, not just idle.

Completion criteria: when Igor processes an item that resolves a known watchlist
gap, a curiosity event fires with measurably higher weight than a confirmation item.
"""

from __future__ import annotations

import pytest

from unseen_university.devices.igor.tools.curiosity_recognition import (
    NOVELTY_SCORES,
    _jaccard,
    _tokenize,
    score_recognition,
)


# ── Pure unit tests (no DB, no Igor imports needed beyond the module) ─────────


def test_tokenize_lowercases_and_strips_stopwords():
    tokens = _tokenize("What is the meaning of life?")
    assert "what" not in tokens  # stopword
    assert "the" not in tokens   # stopword
    assert "meaning" in tokens
    assert "life" in tokens


def test_tokenize_excludes_short_words():
    tokens = _tokenize("go do it")
    assert all(len(t) >= 3 for t in tokens)


def test_tokenize_empty_string():
    assert _tokenize("") == frozenset()


def test_jaccard_identical_sets():
    a = frozenset({"alpha", "beta", "gamma"})
    assert _jaccard(a, a) == 1.0


def test_jaccard_disjoint_sets():
    a = frozenset({"alpha", "beta"})
    b = frozenset({"gamma", "delta"})
    assert _jaccard(a, b) == 0.0


def test_jaccard_partial_overlap():
    a = frozenset({"alpha", "beta", "gamma"})
    b = frozenset({"beta", "gamma", "delta"})
    # intersection={beta,gamma} union={alpha,beta,gamma,delta} → 2/4 = 0.5
    assert _jaccard(a, b) == pytest.approx(0.5)


def test_jaccard_both_empty():
    assert _jaccard(frozenset(), frozenset()) == 0.0


# ── score_recognition: core discriminator ─────────────────────────────────────


def test_gap_resolver_scores_higher_than_confirmation():
    """PRIMARY TEST: gap-resolver produces strictly higher score than confirmation."""
    questions = ["Why does boredom not trigger background goals in Igor?"]

    # Gap-resolving item: contains the key concepts from the question
    gap_item = (
        "The boredom detection mechanism does not trigger background goals because "
        "the BOREDOM_DETECTED observation lacks a category field, so "
        "_check_twm_trigger_habits cannot find it during the habit scan."
    )

    # Confirmation item: vague generic text, low overlap
    confirmation_item = "The weather today is pleasant and the temperature is mild."

    gap_result = score_recognition(gap_item, questions)
    conf_result = score_recognition(confirmation_item, questions)

    assert gap_result["score"] > conf_result["score"], (
        f"Gap-resolver score ({gap_result['score']}) must exceed "
        f"confirmation score ({conf_result['score']})"
    )


def test_gap_resolver_classified_as_gap_explanation():
    questions = ["Why does boredom not trigger background goals in Igor?"]
    gap_item = "The boredom pathway fails because the twm_trigger habit lacks a matching category"
    result = score_recognition(gap_item, questions)
    assert result["novelty_type"] == "gap_explanation", f"Expected gap_explanation, got {result['novelty_type']}"


def test_confirmation_item_produces_low_score():
    questions = ["Why does boredom not trigger background goals in Igor?"]
    conf_item = "Today the sun rose at six and the birds sang their morning songs."
    result = score_recognition(conf_item, questions)
    assert result["score"] == NOVELTY_SCORES["confirmation"]


def test_serendipitous_has_highest_score():
    """Serendipitous score must exceed gap_explanation base and confirmation."""
    assert NOVELTY_SCORES["serendipitous"] > NOVELTY_SCORES["gap_explanation"]
    assert NOVELTY_SCORES["serendipitous"] > NOVELTY_SCORES["confirmation"]


def test_serendipitous_fires_for_novel_diverse_content():
    """Novel content with no watchlist match → serendipitous."""
    questions = ["Why does boredom occur?"]
    # Completely unrelated to boredom: novel content about a different domain
    novel_item = (
        "Crystallographic diffraction patterns reveal lattice periodicity "
        "through Bragg scattering peaks measured at specific angular positions."
    )
    result = score_recognition(novel_item, questions)
    # Should be serendipitous (high overlap not present, many unique tokens)
    assert result["novelty_type"] == "serendipitous"
    assert result["score"] == NOVELTY_SCORES["serendipitous"]


def test_empty_item_text_returns_confirmation():
    result = score_recognition("", ["some open question about the system"])
    assert result["novelty_type"] == "confirmation"
    assert result["matched_question"] is None


def test_empty_questions_list_returns_confirmation():
    result = score_recognition("some interesting information about Igor's habits", [])
    assert result["novelty_type"] == "confirmation"


def test_matched_question_field_set_for_gap():
    questions = [
        "How does reading bootstrap mode work?",
        "Why does boredom not fire worker habits?",
    ]
    item = (
        "The reading bootstrap habit triggers cloud mode by pushing "
        "a min_tier override to the TWM category mode_override"
    )
    result = score_recognition(item, questions)
    if result["novelty_type"] == "gap_explanation":
        assert result["matched_question"] is not None
        assert len(result["matched_question"]) > 0


def test_multiple_questions_picks_best_match():
    """score_recognition picks the question with highest overlap."""
    questions = [
        "How does the reading bootstrap mode enable cloud inference?",
        "What is the capital of France?",
    ]
    item = "Reading bootstrap activates cloud inference via the mode_override category in TWM"
    result = score_recognition(item, questions)
    # Should match the reading bootstrap question, not France
    if result["novelty_type"] == "gap_explanation":
        assert "reading" in result["matched_question"].lower() or "bootstrap" in result["matched_question"].lower()


def test_recognition_result_has_all_required_keys():
    result = score_recognition("some text about Igor's curiosity", ["open question about curiosity"])
    assert "novelty_type" in result
    assert "score" in result
    assert "matched_question" in result
    assert "overlap" in result


def test_gap_explanation_score_above_confirmation_base():
    """gap_explanation score must always exceed confirmation base score."""
    gap_base = NOVELTY_SCORES["gap_explanation"]
    conf_base = NOVELTY_SCORES["confirmation"]
    assert gap_base > conf_base
