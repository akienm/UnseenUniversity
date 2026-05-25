"""
Tests for reading_measure.py — T-reading-measure-baseline.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_fake_cortex(node_count=10, edge_count=25, embedded=5, activated=3):
    """Build a mock Cortex that returns deterministic stats."""
    cortex = MagicMock()

    # _conn() returns a context manager
    conn_ctx = MagicMock()
    conn = MagicMock()
    conn_ctx.__enter__ = MagicMock(return_value=conn)
    conn_ctx.__exit__ = MagicMock(return_value=False)
    cortex._conn.return_value = conn_ctx

    # type_counts query
    type_row = MagicMock()
    type_row.__getitem__ = lambda self, k: {
        "memory_type": "FACTUAL",
        "cnt": node_count,
    }[k]

    # fetchall for type_counts returns a list of row-like dicts
    def side_effect_execute(sql, params=None):
        result = MagicMock()
        if "GROUP BY memory_type" in sql:
            row = {"memory_type": "FACTUAL", "cnt": node_count}
            result.fetchall.return_value = [row]
        elif "COUNT(*) as cnt FROM interpretive_edges" in sql:
            result.fetchone.return_value = {"cnt": edge_count}
        elif "embedding IS NOT NULL" in sql:
            result.fetchone.return_value = {"cnt": embedded}
        elif "AVG(activation_count)" in sql:
            result.fetchone.return_value = {
                "avg_act": 0.3,
                "max_act": 5,
                "activated": activated,
            }
        elif "LEFT JOIN interpretive_edges" in sql:
            hub = {
                "id": "NODE123",
                "edges": 42,
                "snippet": "Neocortex hierarchy prediction memory storage.",
            }
            result.fetchall.return_value = [hub]
        else:
            result.fetchall.return_value = []
            result.fetchone.return_value = None
        return result

    conn.execute.side_effect = side_effect_execute
    return cortex


def test_reading_graph_baseline_format():
    """_format_report returns expected fields."""
    from wild_igor.igor.tools import reading_measure

    stats = {
        "node_count": 10,
        "type_counts": {"FACTUAL": 10},
        "edge_count": 25,
        "density": 2.5,
        "embedded_count": 5,
        "emb_pct": 50.0,
        "avg_activation": 0.3,
        "max_activation": 5,
        "activated_count": 3,
        "activation_reach_pct": 30.0,
        "top_hubs": [
            {"id": "N1", "edges": 42, "snippet": "Neocortex hierarchy prediction."}
        ],
    }
    report = reading_measure._format_report("On Intelligence", stats)

    assert "Reading Graph Baseline" in report
    assert "On Intelligence" in report
    assert "density=2.50" in report
    assert "50.0% coverage" in report
    assert "Top 5 concept hubs" in report


def test_gather_stats_no_nodes():
    """_gather_stats returns error dict when book has no nodes."""
    from wild_igor.igor.tools import reading_measure

    fake_cortex = MagicMock()
    conn_ctx = MagicMock()
    conn = MagicMock()
    conn_ctx.__enter__ = MagicMock(return_value=conn)
    conn_ctx.__exit__ = MagicMock(return_value=False)
    fake_cortex._conn.return_value = conn_ctx

    # Return empty type_counts
    result = MagicMock()
    result.fetchall.return_value = []
    conn.execute.return_value = result

    stats = reading_measure._gather_stats(fake_cortex, "Nonexistent Book")
    assert "error" in stats
    assert "No nodes found" in stats["error"]


def test_reading_graph_baseline_registered():
    """Tool is registered in the registry."""
    from lab.utility_closet.registry import registry

    tool = registry.get("reading_graph_baseline")
    assert tool is not None
    assert tool.name == "reading_graph_baseline"
