"""
pattern_intercept.py — Level 2 ($0) pattern-match intercept for the inference proxy.

Before routing to any cloud provider, checks archivist.knowledge_patterns for a
matching confirmed habit node. If found, returns the compiled response directly and
adds a compiled routing rule pointing future identical requests to local Ollama.

This implements the Graph-Tree Caching principle from the architecture:
  "Successful workflows are compiled directly into durable memory nodes"
  "Proxy intercepts signature → directs compiled habit node to local 7B ($0)"

Matching: fuzzy keyword overlap (no embeddings required for v1). A pattern
matches if the normalized query shares ≥N_MATCH keywords with the stored
pattern_text.

The intercept is a no-op when:
  - Postgres is unavailable (graceful degradation)
  - archivist schema not yet initialized
  - No pattern reaches the similarity threshold
"""

from __future__ import annotations
from unseen_university.identity import home_db_url

import hashlib
import logging
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from unseen_university.devices.inference.shim import InferenceRequest, InferenceResponse

log = logging.getLogger(__name__)

_MIN_KEYWORD_OVERLAP = int(os.environ.get("PATTERN_INTERCEPT_MIN_KEYWORDS", "4"))
_MIN_HIT_COUNT = int(os.environ.get("PATTERN_INTERCEPT_MIN_HITS", "3"))


# ── Text normalization ────────────────────────────────────────────────────────


def _keywords(text: str) -> set[str]:
    """Extract meaningful keywords from text (lower-case, skip stop words)."""
    _STOPWORDS = {
        "a", "an", "the", "is", "it", "in", "on", "at", "to", "for",
        "of", "and", "or", "but", "with", "this", "that", "be", "are",
        "was", "were", "has", "have", "had", "do", "does", "did", "not",
        "from", "by", "as", "if", "so", "we", "you", "i", "they", "he", "she",
    }
    words = re.findall(r"[a-z0-9_]{3,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _similarity(query: str, pattern: str) -> float:
    """Jaccard-style overlap between keyword sets. Returns 0.0–1.0."""
    qk = _keywords(query)
    pk = _keywords(pattern)
    if not qk or not pk:
        return 0.0
    intersection = len(qk & pk)
    union = len(qk | pk)
    return intersection / union if union else 0.0


# ── Pattern lookup ────────────────────────────────────────────────────────────


@dataclass
class PatternMatch:
    pattern_id: int
    pattern_text: str
    response_text: str
    hit_count: int
    similarity: float


def find_pattern_match(
    query_text: str,
    db_url: str | None = None,
    min_hits: int = _MIN_HIT_COUNT,
    min_keywords: int = _MIN_KEYWORD_OVERLAP,
) -> PatternMatch | None:
    """
    Look up archivist.knowledge_patterns for a match to query_text.

    Returns the best match above threshold, or None. Gracefully returns None
    if Postgres is unavailable or the archivist schema doesn't exist yet.
    """
    if db_url is None:
        db_url = home_db_url()

    try:
        import psycopg2
        import psycopg2.extras

        conn = psycopg2.connect(db_url, connect_timeout=3)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT id, pattern_text, response_text, hit_count
                   FROM archivist.knowledge_patterns
                   WHERE hit_count >= %s
                   ORDER BY hit_count DESC
                   LIMIT 200""",
                (min_hits,),
            )
            rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        log.debug("pattern_intercept: DB unavailable — %s", exc)
        return None

    best: PatternMatch | None = None
    query_kw = _keywords(query_text)

    for row in rows:
        pattern_kw = _keywords(row["pattern_text"])
        if not pattern_kw:
            continue
        overlap = len(query_kw & pattern_kw)
        if overlap < min_keywords:
            continue
        similarity = overlap / len(query_kw | pattern_kw) if (query_kw | pattern_kw) else 0.0
        if best is None or similarity > best.similarity:
            best = PatternMatch(
                pattern_id=row["id"],
                pattern_text=row["pattern_text"],
                response_text=row["response_text"],
                hit_count=row["hit_count"],
                similarity=similarity,
            )

    if best:
        log.info(
            "pattern_intercept: match found (id=%d hit_count=%d similarity=%.2f)",
            best.pattern_id, best.hit_count, best.similarity,
        )
    return best


def record_hit(pattern_id: int, db_url: str | None = None) -> None:
    """Increment hit_count and update last_hit_at for a matched pattern."""
    if db_url is None:
        db_url = home_db_url()
    try:
        import psycopg2

        conn = psycopg2.connect(db_url, connect_timeout=3)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE archivist.knowledge_patterns "
                "SET hit_count = hit_count + 1, last_hit_at = now() "
                "WHERE id = %s",
                (pattern_id,),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        log.debug("pattern_intercept: record_hit failed (non-fatal): %s", exc)


# ── Intercept entrypoint ──────────────────────────────────────────────────────


def try_intercept(req: "InferenceRequest", db_url: str | None = None) -> "InferenceResponse | None":
    """
    Check pattern cache before cloud dispatch. Returns a cached InferenceResponse
    if a match is found, or None if the request should proceed to cloud routing.

    Callers (InferenceDevice.dispatch) check this first:
        cached = try_intercept(req)
        if cached:
            return cached   # $0, no cloud call
        return _cloud_dispatch(req)
    """
    # Build query text from all user messages + system prompt
    query_parts = [req.system] if req.system else []
    for msg in req.messages:
        if msg.get("role") in ("user", "system"):
            query_parts.append(msg.get("content", ""))
    query_text = " ".join(query_parts)

    if len(query_text) < 20:
        return None  # Too short to match meaningfully

    match = find_pattern_match(query_text, db_url=db_url)
    if match is None:
        return None

    # Record the cache hit
    record_hit(match.pattern_id, db_url=db_url)

    from unseen_university.devices.inference.shim import InferenceResponse

    log.info(
        "pattern_intercept: cache hit — returning compiled response "
        "(id=%d, hit_count=%d, sim=%.2f) — $0",
        match.pattern_id, match.hit_count, match.similarity,
    )

    return InferenceResponse(
        text=match.response_text,
        model="archivist-pattern-cache",
        finish_reason="stop",
        input_tokens=0,
        output_tokens=0,
        cost_estimate=0.0,
        raw={"pattern_id": match.pattern_id, "similarity": match.similarity, "source": "pattern_intercept"},
    )
