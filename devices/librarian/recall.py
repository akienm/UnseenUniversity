"""
recall.py — Librarian recall(X) endpoint.

'What do I know about X?' API for all agents. Inference-free in the common
case; optional escalation for nuance (writes back so next recall is cheaper).

Query path:
  1. FTS on narrative + tags (free)
  2. Vector similarity via pre-computed embeddings (math, no inference)
  3. Graph walk from hits via typed interpretive_edges
  4. RRF merge of three result sets
  5. Link following: file paths read, URLs fetched (no inference)
  6. Optional inference escalation → writes back to memory
  7. Synthesis on small pre-filtered set (only when escalate=True)

API: recall(query, limit=10, escalate=False) → RecallResult

Design: inference only in step 6-7 (escalation). Steps 1-5 are deterministic.
Every escalation result is written back via memory_writer so the next recall
for the same topic needs no inference.

D-shared-memory-service-2026-05-28
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MAX_FTS_RESULTS = 50
_MAX_VECTOR_RESULTS = 20
_MAX_GRAPH_HOPS = 2
_LINK_TIMEOUT_S = 5


# ── Result types ───────────────────────────────────────────────────────────────


@dataclass
class MemoryHit:
    memory_id: str
    narrative: str
    tags: list[str]
    score: float  # RRF-merged relevance
    source: str  # "fts" | "vector" | "graph"
    linked_content: str | None = None  # from link following


@dataclass
class RecallResult:
    query: str
    hits: list[MemoryHit] = field(default_factory=list)
    synthesis: str | None = None  # only when escalate=True
    from_cache: bool = False
    inference_used: bool = False


# ── Step 1: FTS ───────────────────────────────────────────────────────────────


def _fts_search(cur, query: str, limit: int) -> list[tuple[str, str, list[str], float]]:
    """Full-text search on narrative + tags. Returns [(id, narrative, tags, rank)]."""
    try:
        cur.execute(
            """
            SELECT id, narrative, metadata->>'tags', ts_rank(
                to_tsvector('english', coalesce(narrative, '')),
                plainto_tsquery('english', %s)
            ) AS rank
            FROM clan.memories
            WHERE to_tsvector('english', coalesce(narrative, ''))
                  @@ plainto_tsquery('english', %s)
            ORDER BY rank DESC
            LIMIT %s
            """,
            (query, query, limit),
        )
        rows = cur.fetchall()
        result = []
        for mem_id, narrative, tags_raw, rank in rows:
            tags = json.loads(tags_raw) if tags_raw else []
            result.append((str(mem_id), narrative or "", tags, float(rank)))
        return result
    except Exception as e:
        log.warning("fts_search failed: %s", e)
        return []


# ── Step 2: Vector similarity ──────────────────────────────────────────────────


def _vector_search(
    cur, query_vector: list[float], model: str, limit: int
) -> list[tuple[str, str, list[str], float]]:
    """Cosine similarity against pre-computed embeddings in payloads JSONB.

    Returns [(id, narrative, tags, similarity)].
    No inference — pure math on stored vectors.
    """
    try:
        cur.execute(
            """
            SELECT id, narrative, metadata->>'tags',
                   payloads->'embedding'->>'vector' AS vec_raw
            FROM clan.memories
            WHERE payloads ? 'embedding'
              AND payloads->'embedding'->>'model' = %s
            LIMIT %s
            """,
            (model, _MAX_VECTOR_RESULTS * 5),
        )
        rows = cur.fetchall()

        scored = []
        for mem_id, narrative, tags_raw, vec_raw in rows:
            if not vec_raw:
                continue
            try:
                stored = json.loads(vec_raw)
                sim = _cosine(query_vector, stored)
                tags = json.loads(tags_raw) if tags_raw else []
                scored.append((str(mem_id), narrative or "", tags, sim))
            except Exception:
                continue

        scored.sort(key=lambda x: -x[3])
        return scored[:limit]
    except Exception as e:
        log.warning("vector_search failed: %s", e)
        return []


def _cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Step 3: Graph walk ─────────────────────────────────────────────────────────


def _graph_walk(
    cur, seed_ids: list[str], hops: int = _MAX_GRAPH_HOPS
) -> list[tuple[str, str, list[str], float]]:
    """Walk interpretive_edges from seed_ids up to hops deep.

    Returns [(id, narrative, tags, weight_score)].
    """
    if not seed_ids:
        return []

    visited: set[str] = set(seed_ids)
    frontier: list[str] = list(seed_ids)
    results: list[tuple[str, str, list[str], float]] = []

    for _ in range(hops):
        if not frontier:
            break
        try:
            placeholders = ",".join(["%s"] * len(frontier))
            cur.execute(
                f"""
                SELECT e.to_id, m.narrative, m.metadata->>'tags', e.weight
                FROM clan.interpretive_edges e
                JOIN clan.memories m ON m.id = e.to_id
                WHERE e.from_id IN ({placeholders})
                  AND e.to_id NOT IN ({placeholders})
                ORDER BY e.weight DESC
                LIMIT 20
                """,
                frontier + frontier,
            )
            rows = cur.fetchall()
        except Exception as e:
            log.warning("graph_walk failed: %s", e)
            break

        next_frontier = []
        for to_id, narrative, tags_raw, weight in rows:
            if to_id not in visited:
                visited.add(to_id)
                tags = json.loads(tags_raw) if tags_raw else []
                results.append(
                    (str(to_id), narrative or "", tags, float(weight or 1.0))
                )
                next_frontier.append(to_id)
        frontier = next_frontier

    return results


# ── Step 4: RRF merge ─────────────────────────────────────────────────────────


def _rrf_merge(
    fts: list[tuple], vector: list[tuple], graph: list[tuple], limit: int, k: int = 60
) -> list[tuple[str, str, list[str], float]]:
    """Reciprocal Rank Fusion across three ranked lists."""
    rrf: dict[str, float] = {}
    id_data: dict[str, tuple[str, list[str]]] = {}

    for rank, (mem_id, narrative, tags, _) in enumerate(fts):
        rrf[mem_id] = rrf.get(mem_id, 0) + 1 / (k + rank + 1)
        id_data[mem_id] = (narrative, tags)

    for rank, (mem_id, narrative, tags, _) in enumerate(vector):
        rrf[mem_id] = rrf.get(mem_id, 0) + 1 / (k + rank + 1)
        id_data.setdefault(mem_id, (narrative, tags))

    for rank, (mem_id, narrative, tags, _) in enumerate(graph):
        rrf[mem_id] = rrf.get(mem_id, 0) + 1 / (k + rank + 1)
        id_data.setdefault(mem_id, (narrative, tags))

    ranked = sorted(rrf.items(), key=lambda x: -x[1])[:limit]
    return [
        (mem_id, id_data[mem_id][0], id_data[mem_id][1], score)
        for mem_id, score in ranked
    ]


# ── Step 5: Link following ────────────────────────────────────────────────────


def _follow_link(text: str) -> str | None:
    """Extract and follow first file path or URL in text. No inference."""
    # File path pattern
    path_match = re.search(r"(/[\w./-]{5,}\.(?:py|md|txt|json))", text)
    if path_match:
        p = Path(path_match.group(1))
        if p.exists():
            try:
                return p.read_text(errors="replace")[:2000]
            except OSError:
                pass

    # URL pattern
    url_match = re.search(r"https?://\S{10,}", text)
    if url_match:
        url = url_match.group(0).rstrip(".,)")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Librarian/0.1"})
            with urllib.request.urlopen(req, timeout=_LINK_TIMEOUT_S) as resp:
                return resp.read(4096).decode(errors="replace")
        except Exception:
            pass

    return None


# ── Step 6-7: Escalation + synthesis ─────────────────────────────────────────


def _escalate_and_synthesize(query: str, hits: list[MemoryHit]) -> str:
    """One inference call on the pre-filtered hit set. Synthesizes an answer."""
    try:
        import anthropic

        context = "\n\n".join(f"[{h.memory_id}] {h.narrative[:500]}" for h in hits[:5])
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Query: {query}\n\nMemory context:\n{context}\n\n"
                        "Synthesize a concise answer from these memories only."
                    ),
                }
            ],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        return f"(synthesis failed: {e})"


# ── Public API ────────────────────────────────────────────────────────────────


def recall(
    query: str,
    limit: int = 10,
    escalate: bool = False,
    db_url: str | None = None,
    force_fallback: bool = False,
) -> RecallResult:
    """Recall what the system knows about query.

    Steps 1-5 are inference-free. Step 6-7 (escalation) fires one Haiku call
    and writes the result back so subsequent recalls are cheaper.

    Args:
        query:          Natural language query.
        limit:          Max hits to return.
        escalate:       If True, run inference synthesis on top hits.
        db_url:         PostgreSQL URL; None → IGOR_HOME_DB_URL env var.
        force_fallback: Use hash embeddings (testing, no OpenAI).
    """
    result = RecallResult(query=query)

    url = os.environ.get("IGOR_HOME_DB_URL", "") if db_url is None else db_url
    if not url:
        return result

    try:
        import psycopg2

        conn = psycopg2.connect(url)
    except Exception as e:
        log.warning("recall: db connect failed: %s", e)
        return result

    try:
        # ── 1. FTS ──────────────────────────────────────────────────────────
        with conn.cursor() as cur:
            fts_hits = _fts_search(cur, query, _MAX_FTS_RESULTS)

        # ── 2. Vector similarity ─────────────────────────────────────────────
        try:
            import sys

            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            from devices.scraps.embedding_engine import embed

            emb = embed(query, force_fallback=force_fallback)
            with conn.cursor() as cur:
                vec_hits = _vector_search(
                    cur, emb["vector"], emb["model"], _MAX_VECTOR_RESULTS
                )
        except Exception as e:
            log.warning("recall: vector search failed: %s", e)
            vec_hits = []

        # ── 3. Graph walk ────────────────────────────────────────────────────
        seed_ids = [h[0] for h in fts_hits[:10]] + [h[0] for h in vec_hits[:5]]
        with conn.cursor() as cur:
            graph_hits = _graph_walk(cur, seed_ids)

        # ── 4. RRF merge ─────────────────────────────────────────────────────
        merged = _rrf_merge(fts_hits, vec_hits, graph_hits, limit)

        # ── 5. Link following ─────────────────────────────────────────────────
        hits: list[MemoryHit] = []
        for mem_id, narrative, tags, score in merged:
            source = (
                "fts"
                if any(h[0] == mem_id for h in fts_hits)
                else "vector" if any(h[0] == mem_id for h in vec_hits) else "graph"
            )
            linked = _follow_link(narrative)
            hits.append(
                MemoryHit(
                    memory_id=mem_id,
                    narrative=narrative,
                    tags=tags,
                    score=score,
                    source=source,
                    linked_content=linked,
                )
            )

        result.hits = hits

        # ── 6-7. Optional escalation ─────────────────────────────────────────
        if escalate and hits:
            synthesis = _escalate_and_synthesize(query, hits)
            result.synthesis = synthesis
            result.inference_used = True

            # Write back so next recall is cheaper
            try:
                from devices.librarian.memory_writer import write_memory

                write_memory(
                    content=f"recall synthesis: {query}\n\n{synthesis}",
                    source_agent="librarian-recall",
                    extra_tags=["recall-synthesis", "cached"],
                    db_url=url,
                )
            except Exception as e:
                log.warning("recall: write-back failed: %s", e)

    finally:
        conn.close()

    return result
