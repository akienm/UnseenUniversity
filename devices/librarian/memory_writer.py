"""
memory_writer.py — Librarian memory write endpoint.

Compiles understanding at write time so retrieval is inference-free.

Write path:
  1. Extract tags via one inference call (Haiku).
  2. Call Scraps embedding engine → store as EmbeddingPayload in payloads JSONB.
  3. Accept typed payloads (code, link, primitive) from caller.
  4. Record source_agent for provenance.

Key principle: inference fires once at write time. Everything stored is
pre-computed. Future recall(X) uses stored embeddings and tags — no
write-time inference.

Supersedes T-memory-agent-write-api.

D-shared-memory-service-2026-05-28
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

# ── Payload types ──────────────────────────────────────────────────────────────


@dataclass
class EmbeddingPayload:
    """Pre-computed embedding stored alongside a memory at write time."""

    vector: list[float]
    model: str
    dimension: int
    computed_at: str  # ISO timestamp


@dataclass
class CodePayload:
    language: str
    snippet: str
    file_path: str | None = None


@dataclass
class LinkPayload:
    url: str
    title: str | None = None


@dataclass
class PrimitivePayload:
    value: Any
    type_hint: str | None = None  # "int", "float", "bool", "str"


# ── Tag extraction (one inference call) ───────────────────────────────────────

_TAG_SYSTEM = (
    "You extract concise semantic tags from memory text. "
    "Return only a JSON array of 3-7 short tag strings. "
    'Example: ["Python", "error handling", "async"]'
)


def _extract_tags(content: str, *, force_fallback: bool = False) -> list[str]:
    """Extract tags from content via one Haiku inference call.

    Falls back to keyword extraction when Haiku unavailable.
    """
    if not force_fallback:
        try:
            import anthropic

            client = anthropic.Anthropic()
            msg = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=128,
                system=_TAG_SYSTEM,
                messages=[{"role": "user", "content": content[:2000]}],
            )
            raw = msg.content[0].text.strip()
            tags = json.loads(raw)
            if isinstance(tags, list):
                return [str(t) for t in tags[:7]]
        except Exception:
            pass

    # Keyword fallback: extract capitalized words and common nouns
    words = re.findall(r"\b[A-Z][a-z]{2,}\b|\b[a-z]{4,}\b", content)
    seen: dict[str, int] = {}
    for w in words:
        seen[w.lower()] = seen.get(w.lower(), 0) + 1
    top = sorted(seen, key=lambda k: -seen[k])[:5]
    return top or ["general"]


# ── Write endpoint ────────────────────────────────────────────────────────────


def write_memory(
    content: str,
    source_agent: str,
    memory_type: str = "FACTUAL",
    *,
    extra_tags: list[str] | None = None,
    payloads: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    db_url: str | None = None,
    force_fallback: bool = False,
) -> dict[str, Any]:
    """Write a memory with pre-compiled tags and embedding.

    Args:
        content:      The memory text.
        source_agent: Required — who wrote this memory (provenance).
        memory_type:  FACTUAL | EPISODIC | PROCEDURAL | SEMANTIC
        extra_tags:   Caller-supplied tags merged with inferred tags.
        payloads:     Typed payloads dict; EmbeddingPayload added automatically.
        metadata:     Extra metadata fields merged into the DB row.
        db_url:       PostgreSQL URL; falls back to IGOR_HOME_DB_URL env var.
        force_fallback: Use hash embeddings and keyword tags (testing).

    Returns:
        {"id": str, "tags": list[str], "embedding_model": str, "stored_at": str}
    """
    if not source_agent:
        raise ValueError("source_agent is required")

    # ── 1. Extract tags (one inference call) ──────────────────────────────────
    inferred_tags = _extract_tags(content, force_fallback=force_fallback)
    all_tags = list(dict.fromkeys(inferred_tags + (extra_tags or [])))

    # ── 2. Compute embedding via Scraps ───────────────────────────────────────
    try:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from devices.scraps.embedding_engine import embed

        emb = embed(content, force_fallback=force_fallback)
        emb_payload = EmbeddingPayload(
            vector=emb["vector"],
            model=emb["model"],
            dimension=emb["dimension"],
            computed_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:
        emb_payload = EmbeddingPayload(
            vector=[],
            model=f"error:{e}",
            dimension=0,
            computed_at=datetime.now(timezone.utc).isoformat(),
        )

    # ── 3. Merge payloads ─────────────────────────────────────────────────────
    combined_payloads: dict[str, Any] = dict(payloads or {})
    combined_payloads["embedding"] = asdict(emb_payload)

    # ── 4. Build metadata ─────────────────────────────────────────────────────
    combined_meta: dict[str, Any] = dict(metadata or {})
    combined_meta["tags"] = all_tags
    combined_meta["source_agent"] = source_agent

    # ── 5. Write to DB ────────────────────────────────────────────────────────
    # db_url=None → use env var; db_url="" → no DB (test mode)
    url = os.environ.get("IGOR_HOME_DB_URL", "") if db_url is None else db_url
    stored_at = datetime.now(timezone.utc).isoformat()

    if url:
        try:
            import psycopg2
            import psycopg2.extras

            memory_id = str(uuid.uuid4())
            conn = psycopg2.connect(url)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO clan.memories
                        (id, memory_type, narrative, metadata, payloads, source_agent, timestamp)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        memory_id,
                        memory_type,
                        content,
                        psycopg2.extras.Json(combined_meta),
                        psycopg2.extras.Json(combined_payloads),
                        source_agent,
                        stored_at,
                    ),
                )
            conn.commit()
            conn.close()
        except Exception as e:
            memory_id = f"db_error:{e}"
    else:
        memory_id = "no_db"

    return {
        "id": memory_id,
        "tags": all_tags,
        "embedding_model": emb_payload.model,
        "source_agent": source_agent,
        "stored_at": stored_at,
    }
