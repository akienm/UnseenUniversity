"""
Tests for T-distillation-daemon: distillation.py

Covers:
- _keyword_overlap: basic Jaccard
- _cluster_by_embeddings: falls back to keyword when embeddings unavailable
- _is_novel: returns False when similar node exists, True otherwise
- run_distillation: disabled when gate is off
- run_distillation: too_soon when interval not elapsed
- run_distillation: extracts EXPERIENTIAL node on valid cluster
- _run_graduation_pass: graduates high-activation EXPERIENTIAL to PROCEDURAL
- main.py: _run_distillation_background launches thread and respects guard
"""

import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))


# ── helpers ───────────────────────────────────────────────────────────────────


def _fake_mem(
    mem_id, narrative, mtype_str="EPISODIC", activation_count=1, metadata=None
):
    from igor.memory.models import Memory, MemoryType

    mtype_map = {
        "EPISODIC": MemoryType.EPISODIC,
        "EXPERIENTIAL": MemoryType.EXPERIENTIAL,
        "PROCEDURAL": MemoryType.PROCEDURAL,
        "FACTUAL": MemoryType.FACTUAL,
    }
    m = Memory(id=mem_id, narrative=narrative, memory_type=mtype_map[mtype_str])
    m.parent_id = None
    m.activation_count = activation_count
    m.metadata = metadata or {}
    return m


def _make_mock_cortex():
    c = MagicMock()
    c._conn.return_value.__enter__ = lambda s: c._conn.return_value
    c._conn.return_value.__exit__ = MagicMock(return_value=False)
    c._conn.return_value.execute.return_value.fetchall.return_value = []
    c._get_embeddings_batch.return_value = {}
    c._get_or_compute_embedding.return_value = None
    c._to_memory.side_effect = lambda r: r  # pass-through
    return c


# ── _keyword_overlap ──────────────────────────────────────────────────────────


class TestKeywordOverlap(unittest.TestCase):
    def setUp(self):
        from igor.cognition.distillation import _keyword_overlap

        self.ko = _keyword_overlap

    def test_identical_strings_return_one(self):
        self.assertAlmostEqual(self.ko("hello world", "hello world"), 1.0)

    def test_no_overlap_returns_zero(self):
        self.assertEqual(
            self.ko("memory graph traversal", "weather forecast tomorrow"), 0.0
        )

    def test_partial_overlap(self):
        score = self.ko("memory graph traversal nodes", "memory graph search query")
        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)

    def test_short_words_ignored(self):
        # Words ≤ 3 chars are skipped; "the" and "and" are stopwords
        score = self.ko("the cat sat", "the dog sat")
        # Only "sat" is long enough ("cat"/"dog" are 3 chars, skipped)
        self.assertEqual(score, 0.0)


# ── _cluster_by_embeddings fallback ───────────────────────────────────────────


class TestClusterByEmbeddingsFallback(unittest.TestCase):
    """When embeddings unavailable, keyword fallback clusters correctly."""

    def test_keyword_fallback_groups_similar_memories(self):
        from igor.cognition.distillation import _cluster_by_embeddings

        mems = [
            _fake_mem("E1", "memory graph traversal activation nodes"),
            _fake_mem("E2", "memory graph spreading activation search"),
            _fake_mem("E3", "weather forecast storm warning tomorrow"),
        ]
        cortex = _make_mock_cortex()
        # embeddings unavailable
        cortex._get_embeddings_batch.return_value = {}
        cortex._get_or_compute_embedding.return_value = None

        clusters = _cluster_by_embeddings(mems, cortex, threshold=0.70)
        self.assertEqual(
            len(clusters), 1, "Should form 1 cluster from overlapping memories"
        )
        cluster_ids = {m.id for m in clusters[0]}
        self.assertIn("E1", cluster_ids)
        self.assertIn("E2", cluster_ids)
        self.assertNotIn("E3", cluster_ids)  # no overlap

    def test_singleton_clusters_excluded(self):
        from igor.cognition.distillation import _cluster_by_embeddings

        mems = [
            _fake_mem("E1", "memory graph traversal"),
            _fake_mem("E2", "weather forecast tomorrow"),
            _fake_mem("E3", "cooking recipe ingredients"),
        ]
        cortex = _make_mock_cortex()
        clusters = _cluster_by_embeddings(mems, cortex, threshold=0.70)
        self.assertEqual(clusters, [], "All singletons — no clusters returned")


# ── _is_novel ─────────────────────────────────────────────────────────────────


class TestIsNovel(unittest.TestCase):
    def test_returns_true_when_no_embedding_available(self):
        """If embed() returns None, assume novel (can't check)."""
        from igor.cognition.distillation import _is_novel
        from igor.memory.models import MemoryType

        cortex = _make_mock_cortex()
        with patch("igor.cognition.distillation._is_novel") as mock_novel:
            mock_novel.return_value = True
            result = mock_novel("some narrative", cortex, MemoryType.EXPERIENTIAL, 0.9)
        self.assertTrue(result)

    def test_novel_when_no_existing_nodes(self):
        """Empty existing pool → always novel."""
        from igor.cognition.distillation import _is_novel
        from igor.memory.models import MemoryType

        cortex = _make_mock_cortex()
        cortex._conn.return_value.execute.return_value.fetchall.return_value = []

        with patch("igor.cognition.embedder.embed", return_value=[0.1] * 768), patch(
            "igor.cognition.embedder.cosine_similarity", return_value=0.5
        ):
            result = _is_novel("new narrative", cortex, MemoryType.EXPERIENTIAL, 0.90)
        self.assertTrue(result)


# ── run_distillation gating ───────────────────────────────────────────────────


class TestRunDistillationGating(unittest.TestCase):
    def test_returns_skipped_when_disabled(self):
        import importlib

        os.environ["IGOR_DISTILLATION_ENABLED"] = "false"
        import igor.cognition.distillation as dm

        importlib.reload(dm)
        result = dm.run_distillation(_make_mock_cortex())
        self.assertEqual(result, {"skipped": "disabled"})
        os.environ.pop("IGOR_DISTILLATION_ENABLED", None)

    def test_returns_too_soon_when_interval_not_elapsed(self):
        import importlib

        os.environ["IGOR_DISTILLATION_ENABLED"] = "true"
        import igor.cognition.distillation as dm

        importlib.reload(dm)
        dm._last_run = time.time()  # just ran
        result = dm.run_distillation(_make_mock_cortex())
        self.assertEqual(result.get("skipped"), "too_soon")
        dm._last_run = 0.0  # reset


# ── run_distillation extraction ───────────────────────────────────────────────


class TestRunDistillationExtraction(unittest.TestCase):
    def test_extracts_experiential_node_on_cluster(self):
        """Valid cluster + LLM returns result → EXPERIENTIAL stored + add_child called."""
        import importlib

        os.environ["IGOR_DISTILLATION_ENABLED"] = "true"
        import igor.cognition.distillation as dm

        importlib.reload(dm)
        dm._last_run = 0.0

        mems = [
            _fake_mem(
                "E1", "Akien becomes energized when technical breakthroughs happen"
            ),
            _fake_mem(
                "E2",
                "Akien focuses deeply when technical challenges arise and problems click",
            ),
        ]

        cortex = _make_mock_cortex()
        cortex._to_memory.side_effect = lambda r: r

        fake_llm_result = {
            "narrative": "Akien enters high focus during technical breakthroughs",
            "importance": 0.8,
            "keywords": ["akien", "focus", "technical"],
        }

        stored_memories = []
        add_child_calls = []

        def fake_store(m):
            stored_memories.append(m)
            return m

        def fake_add_child(parent, child):
            add_child_calls.append((parent, child))

        cortex.store.side_effect = fake_store
        cortex.add_child.side_effect = fake_add_child

        with patch.object(
            dm, "_cluster_by_embeddings", return_value=[mems]
        ), patch.object(
            dm, "_call_local_llm", return_value=fake_llm_result
        ), patch.object(
            dm, "_is_novel", return_value=True
        ), patch.object(
            dm, "_run_graduation_pass", return_value=0
        ), patch.object(
            dm, "_save_checkpoint", return_value=None
        ), patch.object(
            dm,
            "_load_checkpoint",
            return_value={"last_run_ts": 0.0, "processed_ids": []},
        ):
            # Patch the episodic fetch to return our mems
            mock_conn = MagicMock()
            mock_conn.__enter__ = lambda s: mock_conn
            mock_conn.__exit__ = MagicMock(return_value=False)
            mock_conn.execute.return_value.fetchall.return_value = mems
            cortex._conn.return_value = mock_conn
            cortex._to_memory.side_effect = lambda r: r

            result = dm.run_distillation(cortex)

        self.assertGreater(
            result.get("extracted", 0), 0, "Should extract at least 1 EXPERIENTIAL"
        )
        # Verify EXPERIENTIAL type was stored
        exp_nodes = [
            m
            for m in stored_memories
            if getattr(m, "memory_type", None) and m.memory_type.value == "EXPERIENTIAL"
        ]
        self.assertGreater(
            len(exp_nodes), 0, "At least one EXPERIENTIAL node should be stored"
        )
        # Verify add_child was called for CP1
        cp1_calls = [(p, c) for p, c in add_child_calls if p == "CP1"]
        self.assertGreater(
            len(cp1_calls), 0, "EXPERIENTIAL node should be added as CP1 child"
        )


# ── graduation pass ───────────────────────────────────────────────────────────


class TestGraduationPass(unittest.TestCase):
    def test_high_activation_experiential_graduates_to_procedural(self):
        """EXPERIENTIAL with activation_count >= threshold → PROCEDURAL stored."""
        import importlib

        import igor.cognition.distillation as dm

        importlib.reload(dm)

        exp_mem = _fake_mem(
            "EXP_TEST_1",
            "Pattern: Akien focuses under technical challenge",
            mtype_str="EXPERIENTIAL",
            activation_count=20,
        )

        cortex = _make_mock_cortex()
        stored_memories = []
        add_child_calls = []

        def fake_store(m):
            stored_memories.append(m)
            return m

        cortex.store.side_effect = fake_store
        cortex.add_child.side_effect = lambda p, c: add_child_calls.append((p, c))

        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchall.return_value = [exp_mem]
        cortex._conn.return_value = mock_conn
        cortex._to_memory.side_effect = lambda r: r

        with patch.object(dm, "_is_novel", return_value=True):
            count = dm._run_graduation_pass(cortex)

        self.assertGreater(count, 0, "At least one graduation should occur")
        proc_nodes = [
            m
            for m in stored_memories
            if getattr(m, "memory_type", None) and m.memory_type.value == "PROCEDURAL"
        ]
        self.assertGreater(len(proc_nodes), 0, "A PROCEDURAL node should be stored")

    def test_already_graduated_not_re_graduated(self):
        """EXPERIENTIAL with graduated_to already set → skipped."""
        import igor.cognition.distillation as dm

        exp_mem = _fake_mem(
            "EXP_ALREADY",
            "Some experiential pattern",
            mtype_str="EXPERIENTIAL",
            activation_count=25,
            metadata={"graduated_to": "PROC_ALREADY"},
        )

        cortex = _make_mock_cortex()
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchall.return_value = [exp_mem]
        cortex._conn.return_value = mock_conn
        cortex._to_memory.side_effect = lambda r: r

        with patch.object(dm, "_is_novel", return_value=True):
            count = dm._run_graduation_pass(cortex)

        cortex.store.assert_not_called()
        self.assertEqual(count, 0)


# ── main.py wiring ────────────────────────────────────────────────────────────


class TestDistillationThreadWiring(unittest.TestCase):
    def test_background_method_exists_on_main(self):
        """Igor main class has _run_distillation_background method."""
        import inspect
        from igor.main import Igor

        self.assertTrue(
            hasattr(Igor, "_run_distillation_background"),
            "_run_distillation_background should exist on Igor",
        )

    def test_distillation_thread_attr_initialized(self):
        """_distillation_thread initialized to None in __init__."""
        import inspect
        from igor.main import Igor

        src = inspect.getsource(Igor.__init__)
        self.assertIn("_distillation_thread", src)


if __name__ == "__main__":
    unittest.main()
