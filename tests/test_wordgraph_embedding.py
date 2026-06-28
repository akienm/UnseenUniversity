"""Tests for word-graph spreading-activation embedding (T-wordgraph-embedding-producer)."""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.scraps.embedding_engine import (
    _WG_DIMENSION,
    _WG_MODEL,
    _cosine,
    _log_wg_comparison,
    _sparse_to_dense,
    _wg_embed,
    embed_batch,
)

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_wg(spread_result: dict) -> MagicMock:
    wg = MagicMock()
    wg.text_to_activation_vector.return_value = spread_result
    return wg


# ── _sparse_to_dense ───────────────────────────────────────────────────────────


class TestSparseToDense:
    def test_output_length_equals_dim(self):
        vec = _sparse_to_dense({"hello": 1.0, "world": 0.5})
        assert len(vec) == _WG_DIMENSION

    def test_custom_dim(self):
        vec = _sparse_to_dense({"hello": 1.0}, dim=64)
        assert len(vec) == 64

    def test_deterministic(self):
        sparse = {"foo": 0.8, "bar": 0.6, "baz": 0.3}
        v1 = _sparse_to_dense(sparse)
        v2 = _sparse_to_dense(sparse)
        assert v1 == v2

    def test_l2_normalized(self):
        vec = _sparse_to_dense({"alpha": 1.0, "beta": 0.7, "gamma": 0.3})
        norm = math.sqrt(sum(v * v for v in vec))
        assert abs(norm - 1.0) < 1e-6

    def test_empty_sparse_returns_zeros(self):
        vec = _sparse_to_dense({})
        assert len(vec) == _WG_DIMENSION
        assert all(v == 0.0 for v in vec)

    def test_single_entry(self):
        vec = _sparse_to_dense({"only": 1.0})
        norm = math.sqrt(sum(v * v for v in vec))
        assert abs(norm - 1.0) < 1e-6


# ── text_to_activation_vector (via WordGraph) ──────────────────────────────────


class TestTextToActivationVector:
    def _make_graph_with_db(self, spread_returns: dict):
        from unseen_university.devices.igor.cognition.word_graph import WordGraph

        wg = WordGraph.__new__(WordGraph)
        wg.spread_from_words = MagicMock(return_value=spread_returns)
        return wg

    def test_empty_text_returns_empty(self):
        from unseen_university.devices.igor.cognition.word_graph import WordGraph

        wg = WordGraph.__new__(WordGraph)
        wg.spread_from_words = MagicMock(return_value={})
        result = wg.text_to_activation_vector("")
        assert result == {}
        wg.spread_from_words.assert_not_called()

    def test_result_is_l2_normalized(self):
        wg = self._make_graph_with_db({"machine": 3.0, "learning": 2.0, "model": 1.0})
        result = wg.text_to_activation_vector("machine learning model")
        norm = math.sqrt(sum(v * v for v in result.values()))
        assert abs(norm - 1.0) < 1e-6

    def test_deterministic_same_graph_state(self):
        fixed_spread = {"neural": 0.9, "network": 0.8, "deep": 0.5}
        wg = self._make_graph_with_db(fixed_spread)
        r1 = wg.text_to_activation_vector("deep neural network")
        # Reset mock to return same value again
        wg.spread_from_words.return_value = fixed_spread
        r2 = wg.text_to_activation_vector("deep neural network")
        assert r1 == r2

    def test_seeds_are_unique_tokens(self):
        wg = self._make_graph_with_db({"word": 1.0})
        wg.text_to_activation_vector("word word word")
        call_args = wg.spread_from_words.call_args
        seed = call_args[0][0] if call_args[0] else call_args[1]["seed_words"]
        # Duplicate tokens collapsed to one seed entry
        assert seed == {"word": 1.0}

    def test_stopwords_excluded_from_seeds(self):
        """tokenize() strips stopwords — 'the', 'a', 'is' should not reach spread_from_words."""
        wg = self._make_graph_with_db({"learning": 1.0})
        wg.text_to_activation_vector("the learning is a process")
        call_args = wg.spread_from_words.call_args[0][0]
        assert "the" not in call_args
        assert "is" not in call_args


# ── _wg_embed ─────────────────────────────────────────────────────────────────


class TestWgEmbed:
    def test_returns_none_when_import_fails(self):
        with patch.dict("sys.modules", {"unseen_university.devices.igor.cognition.word_graph": None}):
            result = _wg_embed(["hello"])
        assert result is None

    def test_returns_none_when_wordgraph_raises(self):
        import unseen_university.devices.igor.cognition.word_graph as wg_mod

        orig = wg_mod.WordGraph
        wg_mod.WordGraph = MagicMock(side_effect=RuntimeError("db down"))
        try:
            result = _wg_embed(["hello world"])
        finally:
            wg_mod.WordGraph = orig
        assert result is None

    def test_result_shape(self):
        import unseen_university.devices.igor.cognition.word_graph as wg_mod

        mock_wg = MagicMock()
        mock_wg.text_to_activation_vector.return_value = {"foo": 1.0, "bar": 0.5}
        orig = wg_mod.WordGraph
        wg_mod.WordGraph = lambda: mock_wg
        try:
            results = _wg_embed(["test text"])
        finally:
            wg_mod.WordGraph = orig

        assert results is not None
        assert len(results) == 1
        r = results[0]
        assert r["model"] == _WG_MODEL
        assert r["dimension"] == _WG_DIMENSION
        assert len(r["vector"]) == _WG_DIMENSION


# ── _cosine ───────────────────────────────────────────────────────────────────


class TestCosine:
    def test_identical_vectors_return_one(self):
        v = [1.0, 0.5, 0.3]
        assert abs(_cosine(v, v) - 1.0) < 1e-6

    def test_orthogonal_vectors_return_zero(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert abs(_cosine(a, b)) < 1e-6

    def test_zero_vector_returns_zero(self):
        assert _cosine([0.0, 0.0], [1.0, 0.5]) == 0.0


# ── embed_batch integration ───────────────────────────────────────────────────


class TestEmbedBatchWgIntegration:
    def test_wg_comparison_logged_on_embed(self, caplog):
        import logging

        mock_wg = MagicMock()
        mock_wg.text_to_activation_vector.return_value = {"hello": 0.9, "world": 0.5}

        import unseen_university.devices.igor.cognition.word_graph as wg_mod

        orig = wg_mod.WordGraph
        wg_mod.WordGraph = lambda: mock_wg
        try:
            with caplog.at_level(
                logging.INFO, logger="unseen_university.devices.scraps.embedding_engine"
            ):
                embed_batch(["hello world"], force_fallback=True)
        finally:
            wg_mod.WordGraph = orig

        assert "wg_training_signal" in caplog.text

    def test_wg_failure_does_not_block_embed(self):
        """WG backend failure must not raise — embed still returns primary result."""
        with patch(
            "unseen_university.devices.scraps.embedding_engine._log_wg_comparison",
            side_effect=RuntimeError("wg exploded"),
        ):
            results = embed_batch(["test"], force_fallback=True)

        assert len(results) == 1
        assert "vector" in results[0]
