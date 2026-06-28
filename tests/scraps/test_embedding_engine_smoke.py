"""Smoke test: verify embed() returns a correctly shaped vector."""

from __future__ import annotations

from unseen_university.devices.scraps.embedding_engine import embed

_VALID_DIMENSIONS = {384, 1536}


def test_embed_returns_required_keys():
    result = embed("test string", force_fallback=True)
    assert "vector" in result
    assert "model" in result
    assert "dimension" in result


def test_embed_vector_length_matches_dimension():
    result = embed("test string", force_fallback=True)
    assert len(result["vector"]) == result["dimension"]


def test_embed_dimension_is_valid():
    result = embed("test string", force_fallback=True)
    assert result["dimension"] in _VALID_DIMENSIONS


def test_embed_live_path():
    """embed() uses OpenAI (1536-dim) when key is set, hash fallback (384-dim) otherwise."""
    result = embed("the embedding engine validates its own shape")
    assert len(result["vector"]) == result["dimension"]
    assert result["dimension"] in _VALID_DIMENSIONS
