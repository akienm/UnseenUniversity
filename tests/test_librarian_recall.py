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


def _make_pg_conn(fts_rows=None, vec_rows=None, edge_rows=None, source_rows=None):
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)

    def fetchall_side_effect():
        # SQL-aware dispatch: inspect the last execute() call to route correctly.
        # Call-count order is unreliable because embedding_engine._wg_embed also
        # calls psycopg2.connect under the patch, injecting an extra fetchall.
        sql = ""
        if cur.execute.call_args:
            sql = cur.execute.call_args[0][0] if cur.execute.call_args[0] else ""
        if "plainto_tsquery" in sql:
            return fts_rows or []
        if "payloads" in sql and "embedding" in sql:
            return vec_rows or []
        if "interpretive_edges" in sql:
            return edge_rows or []
        if "source_agent" in sql:
            return source_rows or []
        return []  # WordGraph or other internal queries

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


def test_memory_hit_trust_tier_defaults_to_zero():
    h = MemoryHit(memory_id="x", narrative="text", tags=["a"], score=0.5, source="fts")
    assert h.trust_tier == 0


# ── trust_tier in recall results ──────────────────────────────────────────────


def test_recall_hit_trust_tier_populated():
    """Hits have trust_tier derived from source_agent in the DB."""
    fts_rows = [("id-1", "Python async patterns", json.dumps(["python"]), 0.9)]
    source_rows = [("id-1", "cc/sprint")]
    conn = _make_pg_conn(fts_rows=fts_rows, source_rows=source_rows)
    with patch("psycopg2.connect", return_value=conn):
        result = recall(
            "python async", db_url="postgresql://fake/db", force_fallback=True
        )
    assert len(result.hits) >= 1
    assert result.hits[0].trust_tier == 1  # "cc/sprint" → tier_1


def test_recall_hit_trust_tier_zero_when_no_source_agent():
    """Hits with no source_agent (legacy) get trust_tier=0."""
    fts_rows = [("id-1", "some narrative", json.dumps(["tag"]), 0.8)]
    source_rows = [("id-1", None)]
    conn = _make_pg_conn(fts_rows=fts_rows, source_rows=source_rows)
    with patch("psycopg2.connect", return_value=conn):
        result = recall("query", db_url="postgresql://fake/db", force_fallback=True)
    assert result.hits[0].trust_tier == 0


def test_recall_hit_trust_tier_zero_when_not_in_source_lookup():
    """Hits missing from source lookup fall back to trust_tier=0."""
    fts_rows = [("id-missing", "narrative", json.dumps(["tag"]), 0.7)]
    source_rows = []  # source lookup returns nothing
    conn = _make_pg_conn(fts_rows=fts_rows, source_rows=source_rows)
    with patch("psycopg2.connect", return_value=conn):
        result = recall("query", db_url="postgresql://fake/db", force_fallback=True)
    assert result.hits[0].trust_tier == 0


# ── min_trust_tier filtering ──────────────────────────────────────────────────


def test_recall_min_trust_tier_keeps_matching_hits():
    """min_trust_tier=2 keeps tier_1 and tier_2 results."""
    fts_rows = [("id-cc", "cc narrative", json.dumps(["t"]), 0.9)]
    source_rows = [("id-cc", "cc/sprint")]
    conn = _make_pg_conn(fts_rows=fts_rows, source_rows=source_rows)
    with patch("psycopg2.connect", return_value=conn):
        result = recall(
            "query",
            min_trust_tier=2,
            db_url="postgresql://fake/db",
            force_fallback=True,
        )
    ids = [h.memory_id for h in result.hits]
    assert "id-cc" in ids  # tier_1 passes min_trust_tier=2


def test_recall_min_trust_tier_filters_tier_0():
    """min_trust_tier filter removes tier_0 (legacy) hits."""
    fts_rows = [
        ("id-legacy", "legacy narrative", json.dumps(["t"]), 0.9),
    ]
    source_rows = [("id-legacy", None)]  # tier_0
    conn = _make_pg_conn(fts_rows=fts_rows, source_rows=source_rows)
    with patch("psycopg2.connect", return_value=conn):
        result = recall(
            "query",
            min_trust_tier=2,
            db_url="postgresql://fake/db",
            force_fallback=True,
        )
    assert result.hits == []


def test_recall_min_trust_tier_filters_tier_3():
    """min_trust_tier=2 removes tier_3 (autonomous) hits."""
    fts_rows = [("id-auto", "autonomous narrative", json.dumps(["t"]), 0.9)]
    source_rows = [("id-auto", "librarian-recall")]  # tier_3
    conn = _make_pg_conn(fts_rows=fts_rows, source_rows=source_rows)
    with patch("psycopg2.connect", return_value=conn):
        result = recall(
            "query",
            min_trust_tier=2,
            db_url="postgresql://fake/db",
            force_fallback=True,
        )
    assert result.hits == []


def test_recall_no_filter_returns_all_tiers():
    """Without min_trust_tier, hits of any trust tier are returned."""
    fts_rows = [
        ("id-1", "narrative 1", json.dumps(["t"]), 0.9),
        ("id-2", "narrative 2", json.dumps(["t"]), 0.8),
    ]
    source_rows = [("id-1", "cc/sprint"), ("id-2", None)]  # tier_1 and tier_0
    conn = _make_pg_conn(fts_rows=fts_rows, source_rows=source_rows)
    with patch("psycopg2.connect", return_value=conn):
        result = recall("query", db_url="postgresql://fake/db", force_fallback=True)
    ids = [h.memory_id for h in result.hits]
    assert "id-1" in ids
    assert "id-2" in ids


# ── T-provenance-write-attribution: derived_from in write-back ────────────────


def test_recall_writeback_includes_derived_from():
    """When escalation writes a synthesis back, derived_from carries hit IDs."""
    fts_rows = [
        ("src-id-1", "Python async patterns", json.dumps(["python"]), 0.9),
        ("src-id-2", "asyncio event loop", json.dumps(["async"]), 0.8),
    ]
    conn = _make_pg_conn(fts_rows=fts_rows)

    captured: dict = {}

    def fake_write_memory(**kwargs):
        captured.update(kwargs)
        return {
            "id": "written-id",
            "tags": [],
            "embedding_model": "test",
            "source_agent": "librarian-recall",
            "stored_at": "now",
        }

    with (
        patch("psycopg2.connect", return_value=conn),
        patch(
            "devices.librarian.recall._escalate_and_synthesize",
            return_value="synthesis text",
        ),
        patch("devices.librarian.recall.write_memory", fake_write_memory),
    ):
        result = recall(
            "python async",
            db_url="postgresql://fake/db",
            escalate=True,
            force_fallback=True,
        )

    assert "derived_from" in captured
    assert isinstance(captured["derived_from"], list)
    assert (
        "src-id-1" in captured["derived_from"] or "src-id-2" in captured["derived_from"]
    )
