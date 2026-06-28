"""
test_goal_formation.py — T-goal-formation-from-conversation (#427)

Tests for the recurrence detection half. Pure detect_candidates() is
tested without I/O; scan_for_recurrence() uses a mocked cortex.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.cognition.goal_formation import (  # noqa: E402
    DEFAULT_MIN_RECURRENCE,
    FormationCandidate,
    _tokenize,
    detect_candidates,
    scan_for_recurrence,
)

# ── _tokenize ────────────────────────────────────────────────────────────────


def test_tokenize_filters_short_and_stopwords():
    tokens = _tokenize("I want to think about word graph stuff today")
    assert "word" in tokens
    assert "graph" in tokens
    assert "stuff" in tokens
    # too short
    assert "to" not in tokens
    # stopword
    assert "want" not in tokens
    assert "today" not in tokens


def test_tokenize_lowercases():
    assert "graph" in _tokenize("Graph GRAPH graph")
    assert "Graph" not in _tokenize("Graph")


def test_tokenize_empty_input():
    assert _tokenize("") == []


# ── detect_candidates: pure logic ────────────────────────────────────────────


def _item(idx: int, text: str, ts: str = "") -> dict:
    return {
        "id": f"mem_{idx}",
        "text": text,
        "timestamp": ts or f"2026-04-{10+idx:02d}T10:00:00",
    }


def test_detect_returns_empty_below_threshold():
    items = [
        _item(1, "thinking about cortex stuff"),
        _item(2, "more cortex thoughts"),
    ]
    # min_recurrence=3 default, only 2 occurrences
    assert detect_candidates(items) == []


def test_detect_finds_recurring_topic():
    items = [
        _item(1, "thinking about cortex memory"),
        _item(2, "cortex search latency check"),
        _item(3, "cortex hot reload broken again"),
        _item(4, "completely unrelated thing about ovens"),
    ]
    cands = detect_candidates(items, min_recurrence=3)
    assert len(cands) >= 1
    assert cands[0].topic == "cortex"
    assert cands[0].recurrence_count == 3
    assert "mem_1" in cands[0].source_memory_ids


def test_detect_records_first_and_last_seen():
    items = [
        _item(1, "graph thing", ts="2026-04-10T10:00:00"),
        _item(2, "graph stuff", ts="2026-04-12T11:00:00"),
        _item(3, "graph notes", ts="2026-04-14T12:00:00"),
    ]
    cands = detect_candidates(items, min_recurrence=3)
    top = cands[0]
    assert top.first_seen == "2026-04-10T10:00:00"
    assert top.last_seen == "2026-04-14T12:00:00"


def test_detect_co_tokens_capture_neighborhood():
    items = [
        _item(1, "cortex search slow today"),
        _item(2, "cortex search latency huge"),
        _item(3, "cortex search latency again"),
    ]
    cands = detect_candidates(items, min_recurrence=3, max_candidates=10)
    top = next(c for c in cands if c.topic == "cortex")
    assert "search" in top.co_tokens


def test_detect_returns_only_top_candidate_by_default():
    items = [
        _item(1, "cortex one cortex two"),
        _item(2, "cortex three rocket one"),
        _item(3, "cortex four rocket two"),
        _item(4, "rocket three rocket four"),
    ]
    cands = detect_candidates(items, min_recurrence=3)
    assert len(cands) == 1


def test_detect_max_candidates_param():
    items = [
        _item(1, "alpha bravo charlie"),
        _item(2, "alpha bravo delta"),
        _item(3, "alpha bravo echo"),
    ]
    cands = detect_candidates(items, min_recurrence=3, max_candidates=5)
    # Both alpha and bravo recur 3x
    topics = {c.topic for c in cands}
    assert "alpha" in topics
    assert "bravo" in topics


def test_detect_ignores_short_tokens_and_stopwords():
    items = [
        _item(1, "I am here today"),
        _item(2, "I am here today"),
        _item(3, "I am here today"),
    ]
    # min_token_len=4, all stopwords filtered → nothing recurs
    assert detect_candidates(items, min_recurrence=3) == []


def test_detect_handles_empty_text_gracefully():
    items = [_item(1, ""), _item(2, ""), _item(3, "")]
    assert detect_candidates(items) == []


def test_detect_ranks_by_recurrence_count():
    items = [
        _item(1, "alpha alpha alpha"),  # alpha appears once per item set-wise
        _item(2, "alpha bravo"),
        _item(3, "alpha bravo"),
        _item(4, "bravo charlie"),
    ]
    # alpha appears in items 1,2,3 = 3 times
    # bravo appears in items 2,3,4 = 3 times
    # tie — both should appear, but recurrence_count rules
    cands = detect_candidates(items, min_recurrence=3, max_candidates=2)
    assert all(c.recurrence_count == 3 for c in cands)


# ── FormationCandidate.to_metadata ───────────────────────────────────────────


def test_metadata_carries_cp1_provisional():
    fc = FormationCandidate(
        topic="cortex",
        recurrence_count=4,
        source_memory_ids=["a", "b"],
        first_seen="2026-04-10",
        last_seen="2026-04-14",
        co_tokens=["search", "memory"],
    )
    md = fc.to_metadata()
    assert md["cp1_provisional"] is True
    assert md["topic"] == "cortex"
    assert md["recurrence_count"] == 4
    assert md["type"] == "goal_formation_candidate"


# ── scan_for_recurrence: with mocked cortex ──────────────────────────────────


def _make_mock_cortex(rows: list[tuple] | None = None):
    cortex = MagicMock()
    conn = MagicMock()
    cortex._db.return_value.__enter__.return_value = conn
    cortex._db.return_value.__exit__.return_value = False
    conn.fetchall.return_value = rows or []
    cortex.twm_push.return_value = 1
    return cortex, conn


def test_scan_empty_db_returns_empty_no_push():
    cortex, _ = _make_mock_cortex(rows=[])
    out = scan_for_recurrence(cortex)
    assert out == []
    assert not cortex.twm_push.called


def test_scan_pushes_top_candidate_to_twm():
    rows = [
        ("mem_1", "cortex search broken", "2026-04-10T10:00:00"),
        ("mem_2", "cortex hot reload", "2026-04-11T10:00:00"),
        ("mem_3", "cortex memory issue", "2026-04-12T10:00:00"),
    ]
    cortex, _ = _make_mock_cortex(rows=rows)
    out = scan_for_recurrence(cortex)
    assert len(out) == 1
    assert out[0].topic == "cortex"
    assert cortex.twm_push.called
    push = cortex.twm_push.call_args
    md = push.kwargs["metadata"]
    assert md["type"] == "goal_formation_candidate"
    assert md["cp1_provisional"] is True
    assert md["topic"] == "cortex"


def test_scan_does_not_push_when_below_threshold():
    rows = [
        ("mem_1", "isolated topic alpha", "2026-04-10T10:00:00"),
        ("mem_2", "different topic bravo", "2026-04-11T10:00:00"),
    ]
    cortex, _ = _make_mock_cortex(rows=rows)
    out = scan_for_recurrence(cortex)
    assert out == []
    assert not cortex.twm_push.called


def test_scan_push_to_twm_can_be_disabled():
    rows = [
        ("mem_1", "cortex search broken", "2026-04-10T10:00:00"),
        ("mem_2", "cortex hot reload", "2026-04-11T10:00:00"),
        ("mem_3", "cortex memory issue", "2026-04-12T10:00:00"),
    ]
    cortex, _ = _make_mock_cortex(rows=rows)
    out = scan_for_recurrence(cortex, push_to_twm=False)
    assert len(out) >= 1
    assert not cortex.twm_push.called


def test_scan_degrades_on_db_failure():
    cortex = MagicMock()
    cortex._db.side_effect = RuntimeError("db down")
    out = scan_for_recurrence(cortex)
    assert out == []


def test_scan_degrades_on_twm_push_failure():
    rows = [
        ("mem_1", "cortex one", "2026-04-10T10:00:00"),
        ("mem_2", "cortex two", "2026-04-11T10:00:00"),
        ("mem_3", "cortex three", "2026-04-12T10:00:00"),
    ]
    cortex, _ = _make_mock_cortex(rows=rows)
    cortex.twm_push.side_effect = RuntimeError("twm down")
    # Should not raise
    out = scan_for_recurrence(cortex)
    assert len(out) == 1


def test_default_min_recurrence_is_three():
    """Threshold tuning — 3 is the floor for 'kept returning to X'."""
    assert DEFAULT_MIN_RECURRENCE == 3
