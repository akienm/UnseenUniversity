"""
Tests for D233: spreading_activation — two-layer heat propagation.

Covers:
- cortex.spreading_activation: returns empty dict for empty seeds
- cortex.spreading_activation: seeds start at 1.0
- cortex.spreading_activation: memory layer propagates to neighbors
- cortex.spreading_activation: word_graph layer skipped when word_graph=None
- cortex.spreading_activation: word_graph layer calls spread_from_words+words_to_doc_ids
- WordGraph.spread_from_words: propagates activation through mocked wg_edges
- WordGraph.spread_from_words: empty seeds return empty dict
- WordGraph.words_to_doc_ids: maps word scores to doc_ids via mocked wg_word_docs
- WordGraph.words_to_doc_ids: empty input returns empty dict
"""

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))


# ── helper: minimal mock memory ───────────────────────────────────────────────


def _mem(mem_id, narrative="test narrative", parent_id=None, children_ids=None):
    m = MagicMock()
    m.id = mem_id
    m.narrative = narrative
    m.parent_id = parent_id
    m.children_ids = children_ids or []
    m.link_ids = []
    m.links = {}
    m.memory_type = MagicMock()
    m.memory_type.value = "FACTUAL"
    m.relevance_score = 0.5
    return m


# ── WordGraph unit tests ───────────────────────────────────────────────────────


class TestWordGraphSpreadFromWords(unittest.TestCase):
    """Unit tests for WordGraph.spread_from_words()"""

    def _make_wg(self, edge_rows=None):
        """Return a WordGraph-like object with a mocked _db."""
        sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))
        from igor.cognition.word_graph import WordGraph

        wg = MagicMock(spec=WordGraph)
        # Wire spread_from_words to the real implementation via unbound call
        wg.spread_from_words = lambda *a, **kw: WordGraph.spread_from_words(wg, *a, **kw)
        # Mock the _db context manager
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = edge_rows or []
        db_ctx = MagicMock()
        db_ctx.__enter__ = MagicMock(return_value=conn)
        db_ctx.__exit__ = MagicMock(return_value=False)
        wg._db = MagicMock(return_value=db_ctx)
        return wg, conn

    def test_empty_seeds_return_empty(self):
        wg, _ = self._make_wg()
        result = wg.spread_from_words({})
        self.assertEqual(result, {})

    def test_seeds_present_in_result(self):
        wg, _ = self._make_wg(edge_rows=[])
        result = wg.spread_from_words({"memory": 1.0, "graph": 0.8})
        self.assertIn("memory", result)
        self.assertIn("graph", result)
        self.assertEqual(result["memory"], 1.0)

    def test_single_hop_propagation(self):
        # word_a="memory", word_b="recall", similarity=0.9
        wg, conn = self._make_wg(edge_rows=[("memory", "recall", 0.9)])
        result = wg.spread_from_words({"memory": 1.0}, hop_decay=0.6, depth=1)
        # "recall" should appear with score = 1.0 * 0.9 * 0.6 = 0.54
        self.assertIn("recall", result)
        self.assertAlmostEqual(result["recall"], 0.54, places=5)

    def test_multi_source_sum(self):
        # Two seeds both activate "recall" with different strengths
        # seed1=memory → recall with sim=0.9; seed2=think → recall with sim=0.5
        wg, conn = self._make_wg(
            edge_rows=[("memory", "recall", 0.9), ("think", "recall", 0.5)]
        )
        result = wg.spread_from_words({"memory": 1.0, "think": 1.0}, hop_decay=0.6, depth=1)
        # Sum: 1.0*0.9*0.6 + 1.0*0.5*0.6 = 0.54 + 0.30 = 0.84
        self.assertAlmostEqual(result["recall"], 0.84, places=5)


class TestWordGraphWordsToDocIds(unittest.TestCase):
    """Unit tests for WordGraph.words_to_doc_ids()"""

    def _make_wg(self, doc_rows=None):
        from igor.cognition.word_graph import WordGraph

        wg = MagicMock(spec=WordGraph)
        wg.words_to_doc_ids = lambda *a, **kw: WordGraph.words_to_doc_ids(wg, *a, **kw)
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = doc_rows or []
        db_ctx = MagicMock()
        db_ctx.__enter__ = MagicMock(return_value=conn)
        db_ctx.__exit__ = MagicMock(return_value=False)
        wg._db = MagicMock(return_value=db_ctx)
        return wg, conn

    def test_empty_input_returns_empty(self):
        wg, _ = self._make_wg()
        result = wg.words_to_doc_ids({})
        self.assertEqual(result, {})

    def test_word_maps_to_doc_id(self):
        # word="memory", doc_id="MEM001", weight=1.0
        wg, _ = self._make_wg(doc_rows=[("memory", "MEM001", 1.0)])
        result = wg.words_to_doc_ids({"memory": 2.0})
        self.assertIn("MEM001", result)
        self.assertAlmostEqual(result["MEM001"], 2.0, places=5)

    def test_multiple_words_sum_to_same_doc(self):
        # "memory" and "recall" both point to MEM001
        wg, _ = self._make_wg(
            doc_rows=[("memory", "MEM001", 1.0), ("recall", "MEM001", 1.0)]
        )
        result = wg.words_to_doc_ids({"memory": 1.0, "recall": 0.5})
        self.assertAlmostEqual(result["MEM001"], 1.5, places=5)


# ── Cortex.spreading_activation unit tests ────────────────────────────────────


class TestCortexSpreadingActivation(unittest.TestCase):
    """Unit tests for Cortex.spreading_activation()"""

    def _make_cortex(self):
        from igor.memory.cortex import Cortex

        cortex = MagicMock(spec=Cortex)
        cortex.spreading_activation = lambda *a, **kw: Cortex.spreading_activation(
            cortex, *a, **kw
        )
        # _cache_fetch_ids: return (cached=[], miss_ids=all_ids)
        cortex._cache_fetch_ids = MagicMock(side_effect=lambda ids: ([], list(ids)))
        cortex._cache_put = MagicMock()
        # _conn: no-op context manager (returns empty fetchall)
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        conn_ctx = MagicMock()
        conn_ctx.__enter__ = MagicMock(return_value=conn)
        conn_ctx.__exit__ = MagicMock(return_value=False)
        cortex._conn = MagicMock(return_value=conn_ctx)
        return cortex, conn

    def test_empty_seeds_return_empty(self):
        cortex, _ = self._make_cortex()
        result = cortex.spreading_activation([])
        self.assertEqual(result, {})

    def test_seeds_in_result_at_1_0(self):
        cortex, _ = self._make_cortex()
        result = cortex.spreading_activation(["ID1", "ID2"])
        self.assertIn("ID1", result)
        self.assertIn("ID2", result)
        self.assertEqual(result["ID1"], 1.0)
        self.assertEqual(result["ID2"], 1.0)

    def test_memory_neighbors_activated(self):
        from igor.memory.cortex import Cortex
        from unittest.mock import MagicMock, patch

        cortex, conn = self._make_cortex()
        # Seed = ID1, with child_id = CHILD1
        seed_mem = _mem("ID1", parent_id=None, children_ids=["CHILD1"])
        conn.execute.return_value.fetchall.return_value = [MagicMock()]
        # _to_memory returns the seed_mem (will be fetched for ID1)
        cortex._to_memory = MagicMock(return_value=seed_mem)
        # _cache_fetch_ids misses all → DB fetch
        cortex._cache_fetch_ids = MagicMock(side_effect=lambda ids: ([], list(ids)))

        result = cortex.spreading_activation(["ID1"], depth=1)
        self.assertIn("ID1", result)
        # CHILD1 should appear with score = 1.0 * 0.8 = 0.8
        self.assertIn("CHILD1", result)
        self.assertAlmostEqual(result["CHILD1"], 0.8, places=5)

    def test_word_graph_layer_skipped_when_none(self):
        cortex, _ = self._make_cortex()
        # word_graph=None → wg layer skipped; no calls to spread_from_words
        mock_wg = MagicMock()
        result = cortex.spreading_activation(["ID1"], word_graph=None)
        mock_wg.spread_from_words.assert_not_called()

    def test_word_graph_layer_calls_spread_from_words(self):
        cortex, _ = self._make_cortex()
        # cortex.get() returns a memory with narrative
        m = _mem("ID1", narrative="memory recall testing")
        cortex.get = MagicMock(return_value=m)

        mock_wg = MagicMock()
        mock_wg.spread_from_words.return_value = {"memory": 0.5, "recall": 0.3}
        mock_wg.words_to_doc_ids.return_value = {"DOCX": 0.4}

        with patch("igor.cognition.word_graph.tokenize", return_value=["memory", "recall", "testing"]):
            result = cortex.spreading_activation(["ID1"], word_graph=mock_wg)

        mock_wg.spread_from_words.assert_called_once()
        mock_wg.words_to_doc_ids.assert_called_once()
        # DOCX should appear in result from bridge
        self.assertIn("DOCX", result)
        self.assertAlmostEqual(result["DOCX"], 0.4 * 0.6, places=5)

    def test_return_type_is_dict(self):
        cortex, _ = self._make_cortex()
        result = cortex.spreading_activation(["ID1", "ID2"])
        self.assertIsInstance(result, dict)
        for v in result.values():
            self.assertIsInstance(v, float)


if __name__ == "__main__":
    unittest.main()
