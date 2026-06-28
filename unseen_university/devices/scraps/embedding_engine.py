"""
embedding_engine.py — Deterministic embedding generation for Scraps.

Accepts text → returns float vector + metadata (model, dimension).
Batch mode: list of strings → list of results.

Primary backend: OpenAI text-embedding-3-small (1536-dim).
Fallback: hash-based deterministic vector (384-dim) when OpenAI unavailable.

Design rules:
- No inference at query time — embeddings computed at write time by caller.
- model metadata (name, dimension) returned with every vector.
- Same input always returns same vector (deterministic per backend).
- Caller owns any DB write — this module only computes.

D-shared-memory-service-2026-05-28
"""

from __future__ import annotations

import hashlib
import logging
import os
import struct
from typing import Any

# ── Constants ──────────────────────────────────────────────────────────────────

_OPENAI_MODEL = "text-embedding-3-small"
_OPENAI_DIMENSION = 1536

_FALLBACK_MODEL = "hash-sha256-384"
_FALLBACK_DIMENSION = 384

_WG_MODEL = "wordgraph-spreading-v1"
_WG_DIMENSION = 512

_log = logging.getLogger(__name__)


# ── Result type ────────────────────────────────────────────────────────────────


def _result(vector: list[float], model: str, dimension: int) -> dict[str, Any]:
    return {"vector": vector, "model": model, "dimension": dimension}


# ── OpenAI backend ─────────────────────────────────────────────────────────────


def _openai_embed(texts: list[str]) -> list[dict[str, Any]]:
    """Embed texts using OpenAI text-embedding-3-small."""
    import openai

    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    client = openai.OpenAI(api_key=api_key)
    response = client.embeddings.create(model=_OPENAI_MODEL, input=texts)
    results = []
    for item in response.data:
        results.append(_result(item.embedding, _OPENAI_MODEL, _OPENAI_DIMENSION))
    return results


# ── Hash fallback backend ──────────────────────────────────────────────────────


def _hash_embed(text: str) -> list[float]:
    """Deterministic 384-float vector from SHA-256 hash of text.

    Not semantically meaningful — for testing and offline use only.
    Produces floats in [-1, 1] by unpacking hash bytes as signed shorts.
    """
    # Generate enough bytes for 384 floats via repeated hashing
    buf = b""
    seed = text.encode()
    while len(buf) < _FALLBACK_DIMENSION * 2:
        seed = hashlib.sha256(seed).digest()
        buf += seed

    # Unpack as signed shorts, normalize to [-1, 1]
    shorts = struct.unpack(f"{_FALLBACK_DIMENSION}h", buf[: _FALLBACK_DIMENSION * 2])
    max_val = 32767.0
    return [s / max_val for s in shorts]


def _fallback_embed(texts: list[str]) -> list[dict[str, Any]]:
    return [
        _result(_hash_embed(t), _FALLBACK_MODEL, _FALLBACK_DIMENSION) for t in texts
    ]


# ── Word-graph backend ─────────────────────────────────────────────────────────


def _sparse_to_dense(sparse: dict[str, float], dim: int = _WG_DIMENSION) -> list[float]:
    """Feature-hash sparse word→float dict into a fixed-size L2-normalized dense vector.

    Uses MD5 for deterministic word→index mapping (PYTHONHASHSEED-independent).
    """
    vec = [0.0] * dim
    for word, score in sparse.items():
        idx = int.from_bytes(hashlib.md5(word.encode()).digest()[:4], "little") % dim
        vec[idx] += score
    norm = sum(v * v for v in vec) ** 0.5
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


def _wg_embed(texts: list[str]) -> list[dict[str, Any]] | None:
    """Word-graph spreading-activation backend.

    Returns None when WordGraph is unavailable (import fails or DB unreachable).
    """
    try:
        from unseen_university.devices.igor.cognition.word_graph import WordGraph
    except ImportError:
        return None
    try:
        wg = WordGraph()
        return [
            _result(
                _sparse_to_dense(wg.text_to_activation_vector(t)),
                _WG_MODEL,
                _WG_DIMENSION,
            )
            for t in texts
        ]
    except Exception:
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _log_wg_comparison(texts: list[str], primary: list[dict[str, Any]]) -> None:
    """Log WG activation alongside primary embedding as a training signal.

    Cosine computed after stride-downsampling primary to WG_DIMENSION.
    Tracks how well the graph approximates the primary embedding over time.
    """
    wg_results = _wg_embed(texts)
    if wg_results is None:
        return
    for text, pr, wr in zip(texts, primary, wg_results):
        pv = pr["vector"]
        wv = wr["vector"]
        # Compare in the smaller of the two spaces to avoid index-out-of-bounds.
        n = min(len(pv), len(wv))
        cosine = _cosine(pv[:n], wv[:n])
        _log.info(
            "wg_training_signal text_len=%d primary_model=%s cosine_vs_wg=%.4f",
            len(text),
            pr["model"],
            cosine,
        )


# ── Public API ─────────────────────────────────────────────────────────────────


def embed(text: str, *, force_fallback: bool = False) -> dict[str, Any]:
    """Embed a single text string.

    Returns: {vector: list[float], model: str, dimension: int}
    """
    return embed_batch([text], force_fallback=force_fallback)[0]


def embed_batch(
    texts: list[str], *, force_fallback: bool = False
) -> list[dict[str, Any]]:
    """Embed a list of text strings.

    Returns: list of {vector: list[float], model: str, dimension: int}
    Each result corresponds to the input text at the same index.
    Side-effect: logs WG comparison signal for graph training (best-effort).
    """
    if not texts:
        return []

    results = None
    if not force_fallback:
        try:
            results = _openai_embed(texts)
        except (ImportError, RuntimeError):
            pass  # fall through to hash fallback
        except Exception:
            pass  # API error — fall through

    if results is None:
        results = _fallback_embed(texts)

    # WG comparison for training signal — never blocks, never raises.
    try:
        _log_wg_comparison(texts, results)
    except Exception:
        pass

    return results
