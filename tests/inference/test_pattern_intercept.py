"""Tests for pattern_intercept.py — Level 2 $0 cache layer."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ── Keyword extraction + similarity ──────────────────────────────────────────


class TestKeywords:
    def test_extracts_words(self):
        from unseen_university.devices.inference.pattern_intercept import _keywords
        kw = _keywords("BaseDevice must implement start and stop methods")
        assert "basedevice" in kw
        assert "implement" in kw
        assert "start" in kw

    def test_skips_stopwords(self):
        from unseen_university.devices.inference.pattern_intercept import _keywords
        kw = _keywords("the cat sat on the mat")
        assert "the" not in kw
        assert "on" not in kw

    def test_skips_short_words(self):
        from unseen_university.devices.inference.pattern_intercept import _keywords
        kw = _keywords("do it")
        assert "do" not in kw
        assert "it" not in kw

    def test_similarity_identical(self):
        from unseen_university.devices.inference.pattern_intercept import _similarity
        assert _similarity("foo bar baz", "foo bar baz") == 1.0

    def test_similarity_disjoint(self):
        from unseen_university.devices.inference.pattern_intercept import _similarity
        assert _similarity("apple orange pear", "hammer screwdriver wrench") == 0.0

    def test_similarity_partial(self):
        from unseen_university.devices.inference.pattern_intercept import _similarity
        score = _similarity("inherit from BaseDevice for all devices", "BaseDevice is the base class")
        assert 0.0 < score < 1.0


# ── find_pattern_match ────────────────────────────────────────────────────────


class TestFindPatternMatch:
    def _mock_rows(self, rows):
        """Patch psycopg2.connect to return fake rows."""
        conn = MagicMock()
        cur = MagicMock()
        cur.__enter__ = lambda s: cur
        cur.__exit__ = MagicMock(return_value=False)
        cur.fetchall.return_value = rows
        conn.cursor.return_value = cur
        return patch("psycopg2.connect", return_value=conn)

    def test_returns_none_when_no_rows(self):
        from unseen_university.devices.inference.pattern_intercept import find_pattern_match
        with self._mock_rows([]):
            result = find_pattern_match("any query", db_url="postgresql://test")
        assert result is None

    def test_returns_match_above_threshold(self):
        from unseen_university.devices.inference.pattern_intercept import find_pattern_match
        rows = [
            {"id": 1, "pattern_text": "inherit from BaseDevice implement start stop rollback methods rack",
             "response_text": "Always inherit from BaseDevice.", "hit_count": 5},
        ]
        with self._mock_rows(rows):
            result = find_pattern_match(
                "Every device must inherit from BaseDevice and implement start stop",
                db_url="postgresql://test", min_keywords=2,
            )
        assert result is not None
        assert result.pattern_id == 1
        assert result.response_text == "Always inherit from BaseDevice."

    def test_picks_best_match(self):
        from unseen_university.devices.inference.pattern_intercept import find_pattern_match
        rows = [
            {"id": 1, "pattern_text": "device inherit start stop rack method",
             "response_text": "resp1", "hit_count": 3},
            {"id": 2, "pattern_text": "device inherit start stop rack method implement rollback",
             "response_text": "resp2", "hit_count": 4},
        ]
        with self._mock_rows(rows):
            result = find_pattern_match(
                "device inherit start stop rack method implement",
                db_url="postgresql://test", min_keywords=2,
            )
        assert result is not None
        assert result.pattern_id == 2  # higher similarity

    def test_returns_none_on_db_error(self):
        from unseen_university.devices.inference.pattern_intercept import find_pattern_match
        with patch("psycopg2.connect", side_effect=Exception("DB down")):
            result = find_pattern_match("any query", db_url="postgresql://test")
        assert result is None


# ── try_intercept ─────────────────────────────────────────────────────────────


class TestTryIntercept:
    def _req(self, content="Implement start stop for BaseDevice rack device"):
        from unseen_university.devices.inference.shim import InferenceRequest
        return InferenceRequest(
            model="gemini-2.0-flash",
            messages=[{"role": "user", "content": content}],
        )

    def test_returns_none_when_no_match(self):
        from unseen_university.devices.inference.pattern_intercept import try_intercept
        with patch("unseen_university.devices.inference.pattern_intercept.find_pattern_match", return_value=None):
            result = try_intercept(self._req())
        assert result is None

    def test_returns_cached_response_on_match(self):
        from unseen_university.devices.inference.pattern_intercept import try_intercept, PatternMatch
        match = PatternMatch(
            pattern_id=42, pattern_text="start stop rack", response_text="Use BaseDevice.",
            hit_count=7, similarity=0.6,
        )
        with patch("unseen_university.devices.inference.pattern_intercept.find_pattern_match", return_value=match):
            with patch("unseen_university.devices.inference.pattern_intercept.record_hit"):
                result = try_intercept(self._req())
        assert result is not None
        assert result.text == "Use BaseDevice."
        assert result.cost_estimate == 0.0
        assert result.model == "archivist-pattern-cache"

    def test_records_hit_on_match(self):
        from unseen_university.devices.inference.pattern_intercept import try_intercept, PatternMatch
        match = PatternMatch(42, "start stop", "resp", 5, 0.5)
        recorded = []
        with patch("unseen_university.devices.inference.pattern_intercept.find_pattern_match", return_value=match):
            with patch("unseen_university.devices.inference.pattern_intercept.record_hit", side_effect=lambda pid, **kw: recorded.append(pid)):
                try_intercept(self._req())
        assert 42 in recorded

    def test_returns_none_for_very_short_query(self):
        from unseen_university.devices.inference.pattern_intercept import try_intercept
        from unseen_university.devices.inference.shim import InferenceRequest
        short_req = InferenceRequest(model="x", messages=[{"role": "user", "content": "hi"}])
        # find_pattern_match should not even be called
        with patch("unseen_university.devices.inference.pattern_intercept.find_pattern_match") as mock_find:
            result = try_intercept(short_req)
        assert result is None
        mock_find.assert_not_called()

    def test_intercept_non_fatal_smoke(self):
        """try_intercept exceptions must not propagate — just returns None."""
        # The device wraps try_intercept in try/except — verified in device.py.
        # This test confirms the pattern_intercept module itself handles DB errors.
        from unseen_university.devices.inference.pattern_intercept import try_intercept
        from unseen_university.devices.inference.shim import InferenceRequest

        req = InferenceRequest(
            model="gemini-2.0-flash",
            messages=[{"role": "user", "content": "What is a BaseDevice?"}],
        )
        with patch("psycopg2.connect", side_effect=Exception("DB down")):
            result = try_intercept(req)
        assert result is None  # graceful degradation, no exception propagated
