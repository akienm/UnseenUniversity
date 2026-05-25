"""
Tests for T-orphan-threshold-fix: cortex.search() orphan rescue.

Verifies that orphan nodes (parent_id=None) are included in the candidate
pool that search() assembles, regardless of traversal pool size.

Integration note: these tests patch at the DB level to inject orphan rows
directly into the orphan rescue query result, verifying the wiring without
requiring a full isolated SQLite instance (which would fight the live pg proxy).
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_cortex_offline():
    """Build a Cortex instance without touching any DB."""
    from igor.memory.cortex import Cortex

    c = Cortex.__new__(Cortex)
    c._instance_id = "test"
    c._cache = {}
    c._cache_order = []
    c._cache_max = 500
    c._SA_DECAY = 0.5
    c._pg_proxy = None
    c._db_path = Path("/tmp/fake.db")
    return c


def _fake_memory(mem_id, narrative, parent_id=None):
    """Return a minimal Memory-like mock."""
    from igor.memory.models import Memory, MemoryType

    m = Memory(
        id=mem_id,
        narrative=narrative,
        memory_type=MemoryType.FACTUAL,
    )
    m.parent_id = parent_id
    return m


class TestOrphanRescueWiring(unittest.TestCase):
    """Verify that orphan rescue SQL fires and results enter all_memories."""

    def test_orphan_rescue_sql_always_runs(self):
        """
        Orphan rescue query runs regardless of traversal pool size.
        With a large traversal pool (> 80 nodes), the old supplement never ran.
        Orphan rescue is separate and unconditional.
        """
        from igor.memory.cortex import Cortex

        c = Cortex.__new__(Cortex)
        c._instance_id = "test"
        c._cache = {}
        c._cache_order = []
        c._cache_max = 500
        c._SA_DECAY = 0.5

        # Build a large traversal pool (> 80 nodes) to prove supplement is skipped
        big_pool = [
            _fake_memory(f"NODE_{i}", f"generic node {i}", parent_id="CP1")
            for i in range(100)
        ]

        orphan = _fake_memory(
            "ORPHAN_RESCUE_1", "orphan unique xyzzy test", parent_id=None
        )

        orphan_row = {
            "id": "ORPHAN_RESCUE_1",
            "narrative": "orphan unique xyzzy test",
            "parent_id": None,
            "memory_type": "factual",
            "children_ids": None,
            "link_ids": None,
            "valence": 0.0,
            "activation_count": 5,
            "friction_history": None,
            "timestamp": "2026-01-01",
            "metadata": None,
            "arousal": 0.0,
            "dominance": 0.3,
            "portable": False,
            "links_weighted": None,
            "last_accessed": None,
            "source": None,
            "confidence": 0.5,
            "context_of_encoding": None,
        }

        calls_made = []

        def fake_execute(sql, params=None):
            calls_made.append(sql.strip())  # store full SQL
            result_mock = MagicMock()
            if "parent_id IS NULL" in sql:
                result_mock.fetchall.return_value = [orphan_row]
            else:
                result_mock.fetchall.return_value = []
            return result_mock

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute = fake_execute

        with patch.object(c, "_conn", return_value=mock_conn), patch.object(
            c, "traverse_from", return_value=big_pool
        ), patch.object(c, "_get_context_anchors", return_value=[]), patch.object(
            c, "get_by_activation", return_value=[]
        ), patch.object(
            c, "_to_memory", return_value=orphan
        ), patch.object(
            c, "_cache_fetch_ids", return_value=([], [])
        ), patch.object(
            c, "_route_types_from_query", return_value=[]
        ), patch.object(
            c, "_spread_activation", side_effect=lambda r, *a, **kw: r
        ), patch.object(
            c, "_apply_recency_frequency_boost", return_value=None
        ), patch.object(
            c, "_touch_last_accessed", return_value=None
        ), patch.object(
            c, "_record_trace", return_value="fake_trail"
        ), patch.object(
            c, "_record_tails", return_value=None
        ), patch.object(
            c, "_apply_trail_training", return_value=None
        ), patch.object(
            c, "_flag_for_reconsolidation", return_value=None
        ):
            results = c.search("xyzzy test", limit=5)

        orphan_sql_fired = any("parent_id IS NULL" in s for s in calls_made)
        self.assertTrue(
            orphan_sql_fired,
            f"Orphan rescue SQL should always fire. Calls seen: {calls_made}",
        )

    def test_orphan_rescue_nodes_included_in_candidates(self):
        """
        Orphan nodes returned by the rescue query enter the candidate pool
        and can score/rank into results via text match.
        """
        from igor.memory.cortex import Cortex

        c = Cortex.__new__(Cortex)
        c._instance_id = "test"
        c._cache = {}
        c._cache_order = []
        c._cache_max = 500
        c._SA_DECAY = 0.5

        # Large traversal pool with no keyword overlap (won't score)
        big_pool = [
            _fake_memory(f"NODE_{i}", f"generic unrelated content {i}", parent_id="CP1")
            for i in range(90)
        ]

        orphan_keyword = "unique_orphan_zeta_keyword"
        orphan = _fake_memory(
            "ORPHAN_POOL_TEST",
            f"orphan memory with {orphan_keyword}",
            parent_id=None,
        )

        orphan_row = {
            "id": "ORPHAN_POOL_TEST",
            "narrative": f"orphan memory with {orphan_keyword}",
            "parent_id": None,
            "memory_type": "factual",
            "children_ids": None,
            "link_ids": None,
            "valence": 0.0,
            "activation_count": 3,
            "friction_history": None,
            "timestamp": "2026-01-01",
            "metadata": None,
            "arousal": 0.0,
            "dominance": 0.3,
            "portable": False,
            "links_weighted": None,
            "last_accessed": None,
            "source": None,
            "confidence": 0.5,
            "context_of_encoding": None,
        }

        def fake_execute(sql, params=None):
            result_mock = MagicMock()
            if "parent_id IS NULL" in sql:
                result_mock.fetchall.return_value = [orphan_row]
            else:
                result_mock.fetchall.return_value = []
            return result_mock

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute = fake_execute

        spread_received_ids = []

        def capture_spread(result, *args, **kwargs):
            spread_received_ids.extend(m.id for m in result)
            return result

        with patch.object(c, "_conn", return_value=mock_conn), patch.object(
            c, "traverse_from", return_value=big_pool
        ), patch.object(c, "_get_context_anchors", return_value=[]), patch.object(
            c, "get_by_activation", return_value=[]
        ), patch.object(
            c, "_to_memory", return_value=orphan
        ), patch.object(
            c, "_cache_fetch_ids", return_value=([], [])
        ), patch.object(
            c, "_route_types_from_query", return_value=[]
        ), patch.object(
            c, "_spread_activation", side_effect=capture_spread
        ), patch.object(
            c, "_apply_recency_frequency_boost", return_value=None
        ), patch.object(
            c, "_touch_last_accessed", return_value=None
        ), patch.object(
            c, "_record_trace", return_value="fake_trail"
        ), patch.object(
            c, "_record_tails", return_value=None
        ), patch.object(
            c, "_apply_trail_training", return_value=None
        ), patch.object(
            c, "_flag_for_reconsolidation", return_value=None
        ):
            results = c.search(orphan_keyword, limit=10)

        result_ids = [m.id for m in results]
        self.assertIn(
            "ORPHAN_POOL_TEST",
            result_ids,
            "Orphan with matching keyword should appear in search results",
        )

    def test_orphan_not_duplicated_if_in_traversal(self):
        """
        If an orphan node is already in the traversal pool, the rescue
        query result is filtered out (dedup by _seen_ids).
        """
        from igor.memory.cortex import Cortex

        c = Cortex.__new__(Cortex)
        c._instance_id = "test"
        c._cache = {}
        c._cache_order = []
        c._cache_max = 500
        c._SA_DECAY = 0.5

        already_in_traversal = _fake_memory(
            "ALREADY_TRAVERSED", "some narrative", parent_id="CP1"
        )

        orphan_row = {
            "id": "ALREADY_TRAVERSED",
            "narrative": "some narrative",
            "parent_id": None,
            "memory_type": "factual",
            "children_ids": None,
            "link_ids": None,
            "valence": 0.0,
            "activation_count": 10,
            "friction_history": None,
            "timestamp": "2026-01-01",
            "metadata": None,
            "arousal": 0.0,
            "dominance": 0.3,
            "portable": False,
            "links_weighted": None,
            "last_accessed": None,
            "source": None,
            "confidence": 0.5,
            "context_of_encoding": None,
        }

        def fake_execute(sql, params=None):
            result_mock = MagicMock()
            if "parent_id IS NULL" in sql:
                result_mock.fetchall.return_value = [orphan_row]
            else:
                result_mock.fetchall.return_value = []
            return result_mock

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute = fake_execute

        all_memories_seen = []

        def capture_and_passthrough(result, *args, **kwargs):
            all_memories_seen.extend(result)
            return result

        with patch.object(c, "_conn", return_value=mock_conn), patch.object(
            c, "traverse_from", return_value=[already_in_traversal]
        ), patch.object(c, "_get_context_anchors", return_value=[]), patch.object(
            c, "get_by_activation", return_value=[]
        ), patch.object(
            c, "_to_memory", return_value=already_in_traversal
        ), patch.object(
            c, "_cache_fetch_ids", return_value=([], [])
        ), patch.object(
            c, "_route_types_from_query", return_value=[]
        ), patch.object(
            c, "_spread_activation", side_effect=capture_and_passthrough
        ), patch.object(
            c, "_apply_recency_frequency_boost", return_value=None
        ), patch.object(
            c, "_touch_last_accessed", return_value=None
        ), patch.object(
            c, "_record_trace", return_value="fake_trail"
        ), patch.object(
            c, "_record_tails", return_value=None
        ), patch.object(
            c, "_apply_trail_training", return_value=None
        ), patch.object(
            c, "_flag_for_reconsolidation", return_value=None
        ):
            c.search("some narrative", limit=10)

        already_traversed_count = sum(
            1 for m in all_memories_seen if m.id == "ALREADY_TRAVERSED"
        )
        self.assertLessEqual(
            already_traversed_count,
            1,
            "Node already in traversal should not be duplicated",
        )


if __name__ == "__main__":
    unittest.main()
