"""
test_wandering_search.py — T-wandering-search

Tests for the wandering-search MVP: seed → spin → step over memory_links
and trigram-similar narratives.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition.wandering_search import (  # noqa: E402
    DEFAULT_SPIN_TOP_K,
    WanderingSearch,
    _parse_links_weighted,
)
from wild_igor.igor.memory.models import Memory, MemoryType  # noqa: E402


def _mem(mem_id, narrative="", links_weighted=None, links=None):
    m = Memory(
        id=mem_id,
        narrative=narrative,
        memory_type=MemoryType.FACTUAL,
        links=links or {},
    )
    if links_weighted is not None:
        m.links_weighted = links_weighted
    return m


class TestParseLinksWeighted:
    def test_dict_passthrough(self):
        assert _parse_links_weighted({"a": 0.5, "b": 0.3}) == {"a": 0.5, "b": 0.3}

    def test_json_string_parsed(self):
        assert _parse_links_weighted('{"a": 0.5, "b": 0.3}') == {"a": 0.5, "b": 0.3}

    def test_empty_returns_empty(self):
        assert _parse_links_weighted("") == {}
        assert _parse_links_weighted(None) == {}

    def test_zero_weights_dropped(self):
        assert _parse_links_weighted({"a": 0.5, "b": 0}) == {"a": 0.5}

    def test_invalid_json_returns_empty(self):
        assert _parse_links_weighted("not-json") == {}


class TestWanderingSearch:
    def _make_cortex(self, memories_by_id=None, db_rows=None):
        cortex = MagicMock()
        memories_by_id = memories_by_id or {}

        def _get(mid):
            return memories_by_id.get(mid)

        cortex.get.side_effect = _get
        cortex._to_memory.side_effect = lambda row: row
        cortex.twm_push.return_value = 1

        db_rows = db_rows or []
        conn = MagicMock()
        conn.fetchall.return_value = db_rows
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=conn)
        ctx.__exit__ = MagicMock(return_value=False)
        cortex._db.return_value = ctx
        return cortex, conn

    def test_initial_state_empty(self):
        cortex, _ = self._make_cortex()
        ws = WanderingSearch(cortex)
        assert ws.focus is None
        assert ws.trace == []

    def test_seed_from_query_too_long_returns_none(self):
        cortex, _ = self._make_cortex()
        ws = WanderingSearch(cortex)
        long = "x" * 200
        assert ws.seed_from_query(long) is None

    def test_seed_from_query_empty_returns_none(self):
        cortex, _ = self._make_cortex()
        ws = WanderingSearch(cortex)
        assert ws.seed_from_query("") is None

    def test_seed_from_query_sets_focus_and_trace(self):
        match = _mem("focus-1", narrative="word ball")
        cortex, conn = self._make_cortex(db_rows=[match])
        ws = WanderingSearch(cortex)
        result = ws.seed_from_query("word")
        assert result is match
        assert ws.focus is match
        assert ws.trace == ["focus-1"]

    def test_seed_from_memory_sets_focus(self):
        target = _mem("m-x", narrative="some narrative")
        cortex, _ = self._make_cortex(memories_by_id={"m-x": target})
        ws = WanderingSearch(cortex)
        result = ws.seed_from_memory("m-x")
        assert result is target
        assert ws.focus is target
        assert ws.trace == ["m-x"]

    def test_seed_from_memory_missing_returns_none(self):
        cortex, _ = self._make_cortex(memories_by_id={})
        ws = WanderingSearch(cortex)
        assert ws.seed_from_memory("nope") is None
        assert ws.focus is None

    def test_spin_with_no_focus_returns_empty(self):
        cortex, _ = self._make_cortex()
        ws = WanderingSearch(cortex)
        assert ws.spin() == []

    def test_spin_via_links_orders_by_weight(self):
        n1 = _mem("n1", narrative="alpha")
        n2 = _mem("n2", narrative="beta")
        n3 = _mem("n3", narrative="gamma")
        focus = _mem(
            "focus",
            narrative="x",
            links_weighted='{"n1": 0.3, "n2": 0.9, "n3": 0.5}',
        )
        cortex, _ = self._make_cortex(memories_by_id={"n1": n1, "n2": n2, "n3": n3})
        # Trigram returns nothing so we isolate the link layer
        cortex._db.return_value.__enter__.return_value.fetchall.return_value = []
        ws = WanderingSearch(cortex)
        ws._focus = focus
        result = ws.spin(top_k=8)
        assert [m.id for m in result] == ["n2", "n3", "n1"]

    def test_spin_caps_at_top_k(self):
        weighted = {f"n{i}": 1.0 - i * 0.05 for i in range(20)}
        weighted_json = ",".join(f'"{k}": {v}' for k, v in weighted.items())
        focus = _mem("focus", narrative="x", links_weighted="{" + weighted_json + "}")
        memories = {f"n{i}": _mem(f"n{i}") for i in range(20)}
        cortex, _ = self._make_cortex(memories_by_id=memories)
        cortex._db.return_value.__enter__.return_value.fetchall.return_value = []
        ws = WanderingSearch(cortex)
        ws._focus = focus
        result = ws.spin(top_k=5)
        assert len(result) == 5

    def test_spin_excludes_focus_from_neighbors(self):
        focus = _mem("focus", narrative="word ball", links_weighted='{"n1": 0.5}')
        n1 = _mem("n1", narrative="similar")
        # Trigram returns the focus itself plus n1; spin must dedupe focus out
        cortex, _ = self._make_cortex(
            memories_by_id={"n1": n1, "focus": focus},
            db_rows=[focus, n1],
        )
        ws = WanderingSearch(cortex)
        ws._focus = focus
        result = ws.spin(top_k=8)
        assert focus.id not in [m.id for m in result]
        assert "n1" in [m.id for m in result]

    def test_spin_dedups_across_layers(self):
        # n1 appears in both link layer and trigram layer — should appear once
        n1 = _mem("n1", narrative="appears in both")
        focus = _mem("focus", narrative="seed", links_weighted='{"n1": 0.7}')
        cortex, _ = self._make_cortex(memories_by_id={"n1": n1}, db_rows=[n1])
        ws = WanderingSearch(cortex)
        ws._focus = focus
        result = ws.spin(top_k=8)
        ids = [m.id for m in result]
        assert ids.count("n1") == 1

    def test_step_moves_focus_and_appends_trace(self):
        m1 = _mem("m1", narrative="first")
        m2 = _mem("m2", narrative="second")
        cortex, _ = self._make_cortex(memories_by_id={"m1": m1, "m2": m2})
        ws = WanderingSearch(cortex)
        ws.seed_from_memory("m1")
        ws.step("m2")
        assert ws.focus is m2
        assert ws.trace == ["m1", "m2"]

    def test_reset_clears_state(self):
        m1 = _mem("m1")
        cortex, _ = self._make_cortex(memories_by_id={"m1": m1})
        ws = WanderingSearch(cortex)
        ws.seed_from_memory("m1")
        assert ws.focus is not None
        ws.reset()
        assert ws.focus is None
        assert ws.trace == []

    def test_twm_surface_pushes_each_neighbor(self):
        focus = _mem("focus", narrative="x")
        n1 = _mem("n1", narrative="alpha")
        n2 = _mem("n2", narrative="beta")
        cortex, _ = self._make_cortex()
        ws = WanderingSearch(cortex)
        ws._focus = focus
        ids = ws.twm_surface([n1, n2])
        assert len(ids) == 2
        assert cortex.twm_push.call_count == 2
        # Verify category="wandering" on both pushes
        for call in cortex.twm_push.call_args_list:
            assert call.kwargs.get("category") == "wandering"

    def test_twm_surface_no_focus_returns_empty(self):
        cortex, _ = self._make_cortex()
        ws = WanderingSearch(cortex)
        assert ws.twm_surface([_mem("n1")]) == []

    def test_twm_surface_empty_returns_empty(self):
        cortex, _ = self._make_cortex()
        ws = WanderingSearch(cortex)
        ws._focus = _mem("focus")
        assert ws.twm_surface([]) == []
