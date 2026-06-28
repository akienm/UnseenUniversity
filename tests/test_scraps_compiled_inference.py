"""Unit tests for compiled-inference Scraps scripts.

Tests cover: purpose_classifier, task_completion_check, habit_tiebreaker.
All tests are pure-Python (no DB, no LLM, no network).
"""

from __future__ import annotations

import pytest

from unseen_university.devices.scraps.purpose_classifier import classify_purpose
from unseen_university.devices.scraps.task_completion_check import check_completion
from unseen_university.devices.scraps.habit_tiebreaker import select_by_overlap

# ── purpose_classifier ────────────────────────────────────────────────────────


class TestClassifyPurpose:
    def test_preference_keyword(self):
        cat, conf = classify_purpose("I prefer to use psycopg2 over raw SQL", "FACTUAL")
        assert cat == "preference"
        assert conf == "HIGH"

    def test_constraint_keyword(self):
        cat, conf = classify_purpose("must not use SQLite in production", "PROCEDURAL")
        assert cat == "constraint"
        assert conf == "HIGH"

    def test_decision_keyword(self):
        cat, conf = classify_purpose(
            "decided to use Postgres for all persistence", "FACTUAL"
        )
        assert cat == "decision"
        assert conf == "HIGH"

    def test_skill_keyword(self):
        cat, conf = classify_purpose(
            "how to write a migration with zero downtime", "PROCEDURAL"
        )
        assert cat == "skill"
        assert conf == "HIGH"

    def test_experience_keyword(self):
        cat, conf = classify_purpose(
            "found that batch inserts are 10x faster", "INTERPRETIVE"
        )
        assert cat == "experience"
        assert conf == "HIGH"

    def test_observation_keyword(self):
        cat, conf = classify_purpose(
            "seems like the dreaming pass tends to surface stale memories",
            "INTERPRETIVE",
        )
        assert cat == "observation"
        assert conf == "HIGH"

    def test_procedural_type_default(self):
        cat, conf = classify_purpose("a" * 50, "PROCEDURAL")
        assert cat == "procedure"
        assert conf == "HIGH"

    def test_factual_type_default(self):
        cat, conf = classify_purpose("a" * 50, "FACTUAL")
        assert cat == "fact"
        assert conf == "HIGH"

    def test_interpretive_type_default(self):
        cat, conf = classify_purpose("a" * 50, "INTERPRETIVE")
        assert cat == "observation"
        assert conf == "HIGH"

    def test_short_narrative_low_confidence(self):
        cat, conf = classify_purpose("short", "FACTUAL")
        assert cat is None
        assert conf == "LOW"

    def test_empty_narrative(self):
        cat, conf = classify_purpose("", "FACTUAL")
        assert cat is None
        assert conf == "LOW"

    def test_unknown_type_no_keyword(self):
        cat, conf = classify_purpose("a" * 50, "UNKNOWN")
        assert cat is None
        assert conf == "LOW"

    def test_keyword_beats_type_default(self):
        # PROCEDURAL default is "procedure" but keyword says "preference"
        cat, _ = classify_purpose("I prefer this approach for most tasks", "PROCEDURAL")
        assert cat == "preference"


# ── task_completion_check ────────────────────────────────────────────────────


class TestCheckCompletion:
    def test_explicit_done_with_overlap(self):
        completed, conf = check_completion(
            ["write the migration file"],
            "I've finished the migration file and it looks good.",
        )
        assert completed is True
        assert conf == "HIGH"

    def test_explicit_not_done(self):
        completed, conf = check_completion(
            ["write the migration file"],
            "I haven't finished the migration yet.",
        )
        assert completed is False
        assert conf == "HIGH"

    def test_completion_word_no_overlap_ambiguous(self):
        # "done" present but no goal word overlap
        completed, conf = check_completion(
            ["deploy the server to production"],
            "The weather is done for today.",
        )
        assert completed is None
        assert conf == "LOW"

    def test_no_signal_ambiguous(self):
        completed, conf = check_completion(
            ["write tests"],
            "Here is the explanation of the algorithm.",
        )
        assert completed is None
        assert conf == "LOW"

    def test_empty_goals(self):
        completed, conf = check_completion([], "Done!")
        assert completed is None
        assert conf == "LOW"

    def test_empty_response(self):
        completed, conf = check_completion(["do the thing"], "")
        assert completed is None
        assert conf == "LOW"

    def test_negative_overrides_positive(self):
        completed, conf = check_completion(
            ["write the report"],
            "The report is done but I haven't finished reviewing it yet.",
        )
        # negative phrase ("haven't finished") overrides positive ("done")
        assert completed is False
        assert conf == "HIGH"

    def test_shipped_keyword(self):
        completed, conf = check_completion(
            ["deploy the new feature"],
            "The new feature has been shipped successfully.",
        )
        assert completed is True
        assert conf == "HIGH"


# ── habit_tiebreaker ──────────────────────────────────────────────────────────


class TestSelectByOverlap:
    def _cand(self, id_: str, narrative: str, score: float = 0.5) -> dict:
        return {"id": id_, "narrative": narrative, "score": score}

    def test_clear_winner(self):
        result = select_by_overlap(
            "show me the current memory usage stats",
            [
                self._cand("h1", "report current memory usage statistics"),
                self._cand("h2", "play background music and ambient sounds"),
            ],
        )
        assert result == "h1"

    def test_too_close_to_call(self):
        result = select_by_overlap(
            "what is the status",
            [
                self._cand("h1", "report status information to the user"),
                self._cand("h2", "display status and report findings"),
            ],
        )
        # Both overlap significantly — ambiguous
        assert result is None

    def test_single_candidate(self):
        result = select_by_overlap(
            "show memory stats",
            [self._cand("h1", "report current memory usage statistics")],
        )
        assert result == "h1"

    def test_empty_candidates(self):
        assert select_by_overlap("anything", []) is None

    def test_empty_input(self):
        assert select_by_overlap("", [self._cand("h1", "something")]) is None

    def test_weak_overlap_returns_none(self):
        result = select_by_overlap(
            "hello",
            [
                self._cand(
                    "h1", "this is a completely unrelated narrative about cooking"
                )
            ],
        )
        assert result is None

    def test_only_short_words_returns_none(self):
        # _word_set only captures words >= 4 chars
        result = select_by_overlap(
            "go do it now",
            [self._cand("h1", "do go it now run")],
        )
        assert result is None
