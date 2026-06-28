"""Tests for devices/librarian/edge_maintenance.py."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from unseen_university.devices.librarian.edge_maintenance import (
    _DEFAULT_EDGE_TYPE,
    _VALID_EDGE_TYPES,
    EdgeMaintenanceWorker,
    backfill_null_edge_types,
    query_edges_by_type,
    strengthen_coactivated_edges,
    validate_edge_type,
)

# ── validate_edge_type ────────────────────────────────────────────────────────


def test_valid_edge_types_accepted():
    for t in _VALID_EDGE_TYPES:
        assert validate_edge_type(t) == t


def test_unknown_edge_type_returns_default():
    assert validate_edge_type("nonsense") == _DEFAULT_EDGE_TYPE


def test_none_edge_type_returns_default():
    assert validate_edge_type(None) == _DEFAULT_EDGE_TYPE


def test_empty_string_returns_default():
    assert validate_edge_type("") == _DEFAULT_EDGE_TYPE


# ── strengthen_coactivated_edges ──────────────────────────────────────────────


def _make_conn(rows=None):
    """Return a mock psycopg2 connection with cursor that yields trace rows."""
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    cur.fetchall.return_value = rows or []
    cur.rowcount = 0  # simulate no existing edge → triggers INSERT
    conn.cursor.return_value = cur
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def test_strengthen_empty_traces_returns_zero():
    conn = _make_conn(rows=[])
    result = strengthen_coactivated_edges(conn)
    assert result == 0


def test_strengthen_uses_default_edge_type():
    """INSERT calls should include edge_type='co-activates'."""
    import json

    nodes = json.dumps([{"node_id": "a"}, {"node_id": "b"}])
    # Need threshold=1 so one trace is enough
    conn = _make_conn(rows=[(nodes,)] * 5)
    strengthen_coactivated_edges(conn, threshold=1, delta=0.1)
    cur = conn.cursor.return_value
    insert_calls = [c for c in cur.execute.call_args_list if "INSERT" in str(c)]
    # Each INSERT should include 'co-activates' as edge_type
    for c in insert_calls:
        assert "co-activates" in str(c)


def test_strengthen_respects_threshold():
    """Pairs below threshold should not create edges."""
    import json

    nodes = json.dumps([{"node_id": "x"}, {"node_id": "y"}])
    conn = _make_conn(rows=[(nodes,)])  # only 1 trace, threshold=3 → no edges
    result = strengthen_coactivated_edges(conn, threshold=3)
    assert result == 0


def test_strengthen_custom_edge_type_used():
    import json

    nodes = json.dumps([{"node_id": "m"}, {"node_id": "n"}])
    conn = _make_conn(rows=[(nodes,)] * 5)
    strengthen_coactivated_edges(conn, edge_type="implements", threshold=1)
    cur = conn.cursor.return_value
    insert_calls = [c for c in cur.execute.call_args_list if "INSERT" in str(c)]
    for c in insert_calls:
        assert "implements" in str(c)


def test_strengthen_invalid_edge_type_uses_default():
    import json

    nodes = json.dumps([{"node_id": "p"}, {"node_id": "q"}])
    conn = _make_conn(rows=[(nodes,)] * 5)
    strengthen_coactivated_edges(conn, edge_type="garbage", threshold=1)
    cur = conn.cursor.return_value
    insert_calls = [c for c in cur.execute.call_args_list if "INSERT" in str(c)]
    for c in insert_calls:
        assert "co-activates" in str(c)


def test_strengthen_exception_returns_zero():
    conn = MagicMock()
    conn.cursor.side_effect = Exception("db error")
    result = strengthen_coactivated_edges(conn)
    assert result == 0


# ── backfill_null_edge_types ──────────────────────────────────────────────────


def test_backfill_returns_rowcount():
    conn = _make_conn()
    conn.cursor.return_value.rowcount = 7
    result = backfill_null_edge_types(conn)
    assert result == 7


def test_backfill_updates_null_edge_types():
    conn = _make_conn()
    backfill_null_edge_types(conn)
    cur = conn.cursor.return_value
    update_calls = [c for c in cur.execute.call_args_list if "UPDATE" in str(c)]
    assert len(update_calls) >= 1
    assert "co-activates" in str(update_calls[0])
    assert "NULL" in str(update_calls[0])


def test_backfill_exception_returns_zero():
    conn = MagicMock()
    conn.cursor.side_effect = Exception("fail")
    assert backfill_null_edge_types(conn) == 0


# ── query_edges_by_type ───────────────────────────────────────────────────────


def test_query_returns_list():
    conn = _make_conn()
    conn.cursor.return_value.fetchall.return_value = [
        ("a", "b", 1.5, "hebbian", "co-activates"),
    ]
    results = query_edges_by_type(conn, "co-activates")
    assert len(results) == 1
    assert results[0]["from_id"] == "a"
    assert results[0]["edge_type"] == "co-activates"


def test_query_unknown_type_uses_default():
    conn = _make_conn()
    conn.cursor.return_value.fetchall.return_value = []
    results = query_edges_by_type(conn, "unknown-type")
    # Should query for 'co-activates' (the default)
    cur = conn.cursor.return_value
    select_calls = [c for c in cur.execute.call_args_list if "SELECT" in str(c)]
    assert any("co-activates" in str(c) for c in select_calls)


def test_query_exception_returns_empty():
    conn = MagicMock()
    conn.cursor.side_effect = Exception("fail")
    assert query_edges_by_type(conn, "co-activates") == []


# ── EdgeMaintenanceWorker ─────────────────────────────────────────────────────


def test_worker_start_stop():
    worker = EdgeMaintenanceWorker(db_url="", interval_s=1000)
    with patch(
        "unseen_university.devices.librarian.edge_maintenance.run_consolidation",
        return_value={"hebbian_count": 0, "backfill_count": 0},
    ):
        worker.start()
        import time

        time.sleep(0.05)
        worker.stop()
    assert worker._thread is not None


# ── Dreaming stub delegation ──────────────────────────────────────────────────


def test_dreaming_stub_delegates_to_librarian():
    """Igor's dreaming._strengthen_coactivated_edges should delegate to Librarian."""
    from unseen_university.devices.igor.cognition.dreaming import _strengthen_coactivated_edges

    conn = _make_conn(rows=[])
    with patch(
        "unseen_university.devices.librarian.edge_maintenance.strengthen_coactivated_edges",
        return_value=0,
    ) as mock_lib:
        _strengthen_coactivated_edges(conn)
        mock_lib.assert_called_once_with(conn)
