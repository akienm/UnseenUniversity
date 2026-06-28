"""T-wg-meta-upsert-latency: word_count writes are batched (G-WG3 pattern).

Before this fix, every WordGraph.index() call upserted the hot 'word_count'
row in wg_meta, hitting up to 5.8s worst-case on row-lock contention. After:
_inc_word_count accumulates in-memory, flushes every _WORD_FLUSH_EVERY
new words or on build_idf / explicit flush_word_count.

Matches the G-WG3 doc_count batching pattern already in place.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.cognition.word_graph import WordGraph


def _fresh_graph():
    g = WordGraph.__new__(WordGraph)
    g._pending_word_count = 0
    g._WORD_FLUSH_EVERY = 50
    g._pending_doc_count = 0
    g._DOC_FLUSH_EVERY = 10
    return g


def test_increment_below_threshold_does_not_write():
    g = _fresh_graph()
    conn = MagicMock()
    g._inc_word_count(conn, 10)
    assert g._pending_word_count == 10
    conn.execute.assert_not_called()


def test_increment_at_threshold_triggers_flush():
    g = _fresh_graph()
    conn = MagicMock()
    g._inc_word_count(conn, 50)
    assert g._pending_word_count == 0
    conn.execute.assert_called_once()
    args = conn.execute.call_args[0]
    assert "word_count" in args[0]
    assert args[1] == ("50", 50)


def test_increment_overshoots_threshold_flushes_total():
    g = _fresh_graph()
    conn = MagicMock()
    g._inc_word_count(conn, 30)
    g._inc_word_count(conn, 40)
    assert g._pending_word_count == 0
    conn.execute.assert_called_once()
    assert conn.execute.call_args[0][1] == ("70", 70)


def test_zero_or_negative_delta_is_noop():
    g = _fresh_graph()
    conn = MagicMock()
    g._inc_word_count(conn, 0)
    g._inc_word_count(conn, -5)
    assert g._pending_word_count == 0
    conn.execute.assert_not_called()


def test_flush_word_count_writes_pending():
    g = _fresh_graph()
    db_mock = MagicMock()
    conn_mock = MagicMock()
    db_mock.return_value.__enter__ = lambda self: conn_mock
    db_mock.return_value.__exit__ = lambda self, *a: False
    g._db = db_mock
    g._pending_word_count = 7

    g.flush_word_count()

    assert g._pending_word_count == 0
    conn_mock.execute.assert_called_once()
    assert conn_mock.execute.call_args[0][1] == ("7", 7)


def test_flush_word_count_noop_when_empty():
    g = _fresh_graph()
    g._db = MagicMock()
    g._pending_word_count = 0

    g.flush_word_count()

    g._db.assert_not_called()


def test_many_small_increments_flush_once_not_many():
    """The reduction in DB writes — 50 increments of 1 word should be 1 flush, not 50."""
    g = _fresh_graph()
    conn = MagicMock()
    for _ in range(50):
        g._inc_word_count(conn, 1)
    assert conn.execute.call_count == 1
