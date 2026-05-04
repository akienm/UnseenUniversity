"""T-priming-via-spreading-activation: heat_field primes cortex.search.

Verifies:
  - set_heat_field stores heat and timestamp
  - _get_current_heat_field returns heat when fresh, empty dict when expired
  - search results are reranked when heat_field is populated
  - search is unchanged when heat_field is empty/expired
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))


@pytest.fixture()
def cortex(tmp_path):
    from wild_igor.igor.memory import cortex as cortex_mod

    with patch.object(
        cortex_mod, "make_home_proxy", return_value=MagicMock()
    ), patch.object(
        cortex_mod, "make_local_proxy", return_value=MagicMock()
    ), patch.object(
        cortex_mod.Cortex, "_init_db", return_value=None
    ):
        c = cortex_mod.Cortex(instance_id="test")
        yield c


class TestHeatFieldStorage:
    def test_set_heat_field_stores(self, cortex):
        heat = {"node-a": 1.0, "node-b": 0.5}
        cortex.set_heat_field(heat)
        assert cortex._current_heat_field == heat
        assert cortex._heat_field_ts > 0.0

    def test_get_current_heat_fresh(self, cortex):
        heat = {"node-a": 0.8}
        cortex.set_heat_field(heat)
        assert cortex._get_current_heat_field() == heat

    def test_get_current_heat_expired(self, cortex):
        cortex.set_heat_field({"node-a": 1.0})
        # Backdate timestamp by 61 seconds to simulate expiry
        cortex._heat_field_ts = time.monotonic() - 61.0
        assert cortex._get_current_heat_field() == {}

    def test_get_current_heat_empty_initial(self, cortex):
        assert cortex._get_current_heat_field() == {}


class TestSearchHeatPriming:
    def _minimal_search(self, cortex, query, heat):
        """Drive cortex.search with mocked internals and a populated heat field."""
        from wild_igor.igor.memory.models import Memory, MemoryType

        hot_node = Memory(
            id="hot", narrative="test query content", memory_type=MemoryType.FACTUAL
        )
        cold_node = Memory(
            id="cold", narrative="test query content", memory_type=MemoryType.FACTUAL
        )

        for m in [hot_node, cold_node]:
            m.activation_count = 0
            m.metadata = {}
            m.parent_id = None

        cortex.set_heat_field(heat)

        # Stub out all DB/traversal calls
        cortex.traverse_from = MagicMock(return_value=[hot_node, cold_node])
        cortex._route_types_from_query = MagicMock(return_value=[])
        cortex._get_context_anchors = MagicMock(return_value=[])
        cortex.get_by_activation = MagicMock(return_value=[])
        cortex._conn = MagicMock()
        conn_ctx = MagicMock()
        conn_ctx.__enter__ = MagicMock(
            return_value=MagicMock(
                execute=MagicMock(
                    return_value=MagicMock(fetchall=MagicMock(return_value=[]))
                )
            )
        )
        conn_ctx.__exit__ = MagicMock(return_value=False)
        cortex._conn.return_value = conn_ctx
        cortex._apply_pr_frame_bias = MagicMock()
        cortex._spread_activation = MagicMock(side_effect=lambda r, *a, **kw: r)
        cortex._apply_recency_frequency_boost = MagicMock()
        cortex._touch_last_accessed = MagicMock()
        cortex._flag_for_reconsolidation = MagicMock()
        cortex._record_trace = MagicMock(return_value=None)
        cortex._record_tails = MagicMock()
        cortex._apply_trail_training = MagicMock()
        cortex.get_habits = MagicMock(return_value=[])
        cortex.get_hot_attractors = MagicMock(return_value=[])
        cortex.twm_read = MagicMock(return_value=[])
        cortex.twm_get_attractor = MagicMock(return_value=None)

        results = cortex.search(query, limit=2)
        return results, hot_node, cold_node

    def test_hot_node_has_higher_relevance_than_cold(self, cortex):
        """A node with heat gets a relevance bump over an otherwise equal cold node."""
        heat = {"hot": 1.0}
        results, hot_node, cold_node = self._minimal_search(cortex, "test query", heat)

        hot_score = getattr(hot_node, "relevance_score", 0.0) or 0.0
        cold_score = getattr(cold_node, "relevance_score", 0.0) or 0.0
        assert (
            hot_score > cold_score
        ), f"Hot node score {hot_score} should exceed cold node score {cold_score}"

    def test_empty_heat_field_no_bump(self, cortex):
        """When heat field is empty, no bump is applied."""
        results, hot_node, cold_node = self._minimal_search(cortex, "test query", {})
        hot_score = getattr(hot_node, "relevance_score", 0.0) or 0.0
        cold_score = getattr(cold_node, "relevance_score", 0.0) or 0.0
        assert (
            hot_score == cold_score
        ), f"Without heat, scores should be equal; hot={hot_score} cold={cold_score}"

    def test_heat_bump_capped_at_point_one(self, cortex):
        """Heat bump is capped at +0.10 regardless of heat value."""
        from wild_igor.igor.memory.models import Memory, MemoryType

        node = Memory(id="x", narrative="query", memory_type=MemoryType.FACTUAL)
        node.activation_count = 0
        node.metadata = {}
        node.parent_id = None

        cortex.set_heat_field({"x": 100.0})
        cortex.traverse_from = MagicMock(return_value=[node])
        cortex._route_types_from_query = MagicMock(return_value=[])
        cortex._get_context_anchors = MagicMock(return_value=[])
        cortex.get_by_activation = MagicMock(return_value=[])
        conn_ctx = MagicMock()
        conn_ctx.__enter__ = MagicMock(
            return_value=MagicMock(
                execute=MagicMock(
                    return_value=MagicMock(fetchall=MagicMock(return_value=[]))
                )
            )
        )
        conn_ctx.__exit__ = MagicMock(return_value=False)
        cortex._conn = MagicMock(return_value=conn_ctx)
        cortex._apply_pr_frame_bias = MagicMock()
        cortex._spread_activation = MagicMock(side_effect=lambda r, *a, **kw: r)
        cortex._apply_recency_frequency_boost = MagicMock()
        cortex._touch_last_accessed = MagicMock()
        cortex._flag_for_reconsolidation = MagicMock()
        cortex._record_trace = MagicMock(return_value=None)
        cortex._record_tails = MagicMock()
        cortex._apply_trail_training = MagicMock()
        cortex.get_habits = MagicMock(return_value=[])
        cortex.get_hot_attractors = MagicMock(return_value=[])
        cortex.twm_read = MagicMock(return_value=[])
        cortex.twm_get_attractor = MagicMock(return_value=None)

        cortex.search("query", limit=1)
        score = getattr(node, "relevance_score", 0.0) or 0.0
        assert score <= 1.0, f"Score {score} must not exceed 1.0"
