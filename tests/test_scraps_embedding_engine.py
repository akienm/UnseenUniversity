"""Tests for devices/scraps/embedding_engine.py."""

from __future__ import annotations

import pytest

from unseen_university.devices.scraps.embedding_engine import (
    _FALLBACK_DIMENSION,
    _FALLBACK_MODEL,
    embed,
    embed_batch,
)


def test_embed_returns_vector_and_metadata():
    result = embed("hello world", force_fallback=True)
    assert "vector" in result
    assert "model" in result
    assert "dimension" in result


def test_embed_vector_length_matches_dimension():
    result = embed("test string", force_fallback=True)
    assert len(result["vector"]) == result["dimension"]
    assert result["dimension"] == _FALLBACK_DIMENSION


def test_embed_fallback_model_name():
    result = embed("test", force_fallback=True)
    assert result["model"] == _FALLBACK_MODEL


def test_embed_deterministic():
    r1 = embed("same input text", force_fallback=True)
    r2 = embed("same input text", force_fallback=True)
    assert r1["vector"] == r2["vector"]


def test_embed_different_inputs_produce_different_vectors():
    r1 = embed("first text", force_fallback=True)
    r2 = embed("second text", force_fallback=True)
    assert r1["vector"] != r2["vector"]


def test_embed_vector_values_in_range():
    result = embed("boundary check", force_fallback=True)
    for v in result["vector"]:
        assert -1.0 <= v <= 1.0


def test_embed_batch_returns_one_result_per_input():
    texts = ["alpha", "beta", "gamma"]
    results = embed_batch(texts, force_fallback=True)
    assert len(results) == len(texts)


def test_embed_batch_empty_input():
    assert embed_batch([], force_fallback=True) == []


def test_embed_batch_single_item():
    results = embed_batch(["solo"], force_fallback=True)
    assert len(results) == 1
    assert len(results[0]["vector"]) == _FALLBACK_DIMENSION


def test_embed_batch_consistent_with_single_embed():
    text = "consistency check"
    single = embed(text, force_fallback=True)
    batch = embed_batch([text], force_fallback=True)
    assert single["vector"] == batch[0]["vector"]
    assert single["model"] == batch[0]["model"]


def test_embed_batch_each_result_has_metadata():
    texts = ["one", "two"]
    results = embed_batch(texts, force_fallback=True)
    for r in results:
        assert "vector" in r
        assert "model" in r
        assert "dimension" in r
        assert len(r["vector"]) == r["dimension"]
