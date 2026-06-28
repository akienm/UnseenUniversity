"""
knn_classifier.py — Graph-first k-NN memory type classifier.

Personality A in the inference competition (T-classifier-graph-first).

Algorithm:
  1. Embed input text via embedder.embed() — file-cached, zero LLM calls.
  2. Query competition.memory_embeddings for K nearest neighbors
     (non-holdout rows only) by cosine similarity.
  3. Vote by memory_type distribution; return majority.
  4. Fallback to FACTUAL when embed fails or competition.memory_embeddings
     is empty.

No LLM calls. Pure graph + embedding lookup.

Usage:
    from devlab.competition.classifiers.knn_classifier import classify, build_index

    # Build the embedding index (run once after ingest):
    build_index()

    # Classify a text:
    memory_type, cloud_calls = classify("Use list comprehensions for fast iteration.")
    # → ("PROCEDURAL", 0)
"""
from __future__ import annotations
from unseen_university.identity import home_db_url

import json
import os
from collections import Counter
from typing import Optional

import psycopg2

K = 5
FALLBACK_TYPE = "FACTUAL"


def _conn():
    return psycopg2.connect(home_db_url())


def _embed(text: str) -> Optional[list[float]]:
    """Call embedder.embed(); returns None on failure (Ollama offline)."""
    try:
        from unseen_university.devices.igor.cognition.embedder import embed

        return embed(text)
    except Exception:
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def classify(text: str, k: int = K) -> tuple[str, int]:
    """Classify text as a memory_type using k-NN on competition.memory_embeddings.

    Returns (memory_type, cloud_calls_count).
    cloud_calls_count is always 0 — this classifier never calls an LLM.
    Fallback: FACTUAL when no embeddings available or embed fails.
    """
    query_vec = _embed(text)
    if query_vec is None:
        return FALLBACK_TYPE, 0

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT e.embedding, m.memory_type "
                "FROM competition.memory_embeddings e "
                "JOIN competition.memories m ON m.id = e.memory_id "
                "WHERE m.holdout = false AND m.memory_type IS NOT NULL "
                "  AND e.embedding IS NOT NULL",
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        return FALLBACK_TYPE, 0

    # Score all neighbors
    scored: list[tuple[float, str]] = []
    for emb_raw, mtype in rows:
        try:
            emb = json.loads(emb_raw) if isinstance(emb_raw, str) else emb_raw
        except Exception:
            continue
        sim = _cosine(query_vec, emb)
        scored.append((sim, mtype))

    if not scored:
        return FALLBACK_TYPE, 0

    # Top-K vote
    scored.sort(key=lambda t: t[0], reverse=True)
    top_k = scored[:k]
    votes = Counter(mtype for _, mtype in top_k)
    winner = votes.most_common(1)[0][0]
    return winner, 0


def build_index(batch_size: int = 100) -> dict:
    """Compute and store embeddings for all competition.memories without embeddings.

    Calls embedder.embed() per narrative (file-cached). Safe to re-run
    (ON CONFLICT DO NOTHING skips already-embedded rows).

    Returns {"embedded": N, "skipped": M, "failed": P}.
    """
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT m.id, m.narrative "
                "FROM competition.memories m "
                "LEFT JOIN competition.memory_embeddings e ON e.memory_id = m.id "
                "WHERE e.memory_id IS NULL AND m.narrative IS NOT NULL "
                "ORDER BY m.id"
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    embedded = skipped = failed = 0
    conn = _conn()
    try:
        for mem_id, narrative in rows:
            if not narrative or not narrative.strip():
                skipped += 1
                continue
            vec = _embed(narrative)
            if vec is None:
                failed += 1
                continue
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO competition.memory_embeddings (memory_id, embedding) "
                        "VALUES (%s, %s) ON CONFLICT (memory_id) DO NOTHING",
                        (mem_id, json.dumps(vec)),
                    )
            embedded += 1
    finally:
        conn.close()

    return {"embedded": embedded, "skipped": skipped, "failed": failed}
