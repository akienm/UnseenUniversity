"""Tests for devices/librarian/recall.py."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from devices.librarian.recall import (
    MemoryHit,
    RecallResult,
    _cosine,
    _follow_link,
    _rrf_merge,
    recall,
)

# ── _cosine ───────────────────────────────────────────────────────────────────


def test_cosine_identical_vectors():
    v = [1.0, 0.0, 0.0]
    assert abs(_cosine(v, v) - 1.0) < 1e-6


def test_cosine_orthogonal_vectors():
    assert abs(_cosine([1.0, 0.0], [0.0, 1.0])) < 1e-6


def test_cosine_zero_vector():
    assert _cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_mismatched_lengths():
    assert _cosine([1.0], [1.0, 2.0]) == 0.0


def test_cosine_symmetric():
    a = [0.6, 0.8]
    b = [0.8, 0.6]
    assert abs(_cosine(a, b) - _cosine(b, a)) < 1e-9


# ── _rrf_merge ────────────────────────────────────────────────────────────────


def _hit(id_, score=1.0):
    return (id_, f"narrative {id_}", ["tag"], score)


def test_rrf_merge_returns_limit():
    fts = [_hit(f"f{i}") for i in range(5)]
    vec = [_hit(f"v{i}") for i in range(5)]
    merged = _rrf_merge(fts, vec, [], limit=4)
    assert len(merged) == 4


def test_rrf_merge_boosts_shared_hits():
    shared = _hit("shared")
    fts = [shared, _hit("fts-only")]
    vec = [shared, _hit("vec-only")]
    merged = _rrf_merge(fts, vec, [], limit=10)
    ids = [m[0] for m in merged]
    # "shared" appears in both lists → highest RRF score → first
    assert ids[0] == "shared"


def test_rrf_merge_empty_inputs():
    assert _rrf_merge([], [], [], limit=10) == []


def test_rrf_merge_graph_hits_included():
    graph = [_hit("g1"), _hit("g2")]
    merged = _rrf_merge([], [], graph, limit=10)
    ids = [m[0] for m in merged]
    assert "g1" in ids and "g2" in ids


# ── _follow_link ──────────────────────────────────────────────────────────────


def test_follow_link_real_file(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hello from file")
    content = _follow_link(f"see {f} for details")
    assert content is not None
    assert "hello from file" in content


def test_follow_link_nonexistent_file():
    content = _follow_link("see /nonexistent/path/file.py for details")
    assert content is None


def test_follow_link_no_link():
    assert _follow_link("nothing here") is None


def test_follow_link_url_timeout():
    # Unreachable URL should not raise — returns None
    content = _follow_link("https://0.0.0.0:1/bad-url-that-cant-connect")
    assert content is None


# ── recall (no-DB mode) ───────────────────────────────────────────────────────


def test_recall_no_db_returns_empty_result():
    result = recall("python async", db_url="")
    assert isinstance(result, RecallResult)
    assert result.hits == []
    assert result.synthesis is None


def test_recall_result_type():
    result = recall("test query", db_url="")
    assert result.query == "test query"
    assert isinstance(result.hits, list)


def test_recall_inference_not_used_without_escalate():
    result = recall("test", db_url="")
    assert result.inference_used is False


# ── recall with mock DB ───────────────────────────────────────────────────────


def _make_pg_conn(fts_rows=None, vec_rows=None, edge_rows=None):
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)

    call_count = [0]

    def fetchall_side_effect():
        n = call_count[0]
        call_count[0] += 1
        if n == 0:
            return fts_rows or []
        if n == 1:
            return vec_rows or []
        return edge_rows or []

    cur.fetchall.side_effect = fetchall_side_effect
    conn.cursor.return_value = cur
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def test_recall_with_fts_hits():
    import psycopg2

    fts_rows = [("id-1", "Python async patterns", json.dumps(["python"]), 0.9)]
    conn = _make_pg_conn(fts_rows=fts_rows)
    with patch("psycopg2.connect", return_value=conn):
        result = recall(
            "python async",
            db_url="postgresql://fake/db",
            force_fallback=True,
        )
    assert len(result.hits) >= 1
    assert result.hits[0].memory_id == "id-1"


def test_recall_fts_hit_source_is_fts():
    fts_rows = [("id-fts", "narrative text", json.dumps(["tag"]), 0.8)]
    conn = _make_pg_conn(fts_rows=fts_rows)
    with patch("psycopg2.connect", return_value=conn):
        result = recall("query", db_url="postgresql://fake/db", force_fallback=True)
    if result.hits:
        assert result.hits[0].source == "fts"


def test_recall_limit_respected():
    fts_rows = [
        (f"id-{i}", f"narrative {i}", json.dumps(["t"]), 1.0) for i in range(20)
    ]
    conn = _make_pg_conn(fts_rows=fts_rows)
    with patch("psycopg2.connect", return_value=conn):
        result = recall(
            "query", limit=3, db_url="postgresql://fake/db", force_fallback=True
        )
    assert len(result.hits) <= 3


# ── MemoryHit / RecallResult ──────────────────────────────────────────────────


def test_memory_hit_fields():
    h = MemoryHit(memory_id="x", narrative="text", tags=["a"], score=0.5, source="fts")
    assert h.memory_id == "x"
    assert h.linked_content is None


def test_recall_result_default_fields():
    r = RecallResult(query="q")
    assert r.hits == []
    assert r.synthesis is None
    assert r.from_cache is False
    assert r.inference_used is False
