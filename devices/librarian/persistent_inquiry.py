"""
persistent_inquiry.py — Librarian persistent question accumulation.

Radar O'Reilly pattern: become_knowledgeable_about(topic) creates a
PersistentQuestion that accumulates hits over time, weighted by novelty
type, and can compress them into a denser current model.

Stored in adc.palace as node_type='persistent_inquiry'.
T-librarian-persistent-inquiry.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone

log = logging.getLogger(__name__)

WEIGHT_TYPE_VALUES: dict[str, float] = {
    "confirmation": 0.3,      # confirms what we already know
    "gap_explanation": 0.6,   # explains a gap in understanding
    "serendipitous": 1.0,     # surprising / unexpected connection
}

_DEFAULT_DB_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

def _topic_slug(topic: str) -> str:
    """Convert topic string to a safe palace path segment."""
    slug = re.sub(r"[^a-z0-9]+", "-", topic.lower().strip()).strip("-")
    return slug or "general"


def _palace_path(topic: str) -> str:
    return f"palace.library.inquiry.{_topic_slug(topic)}"


def _weight_for_type(weight_type: str) -> float:
    """Return numeric weight for a novelty type; unknown types default to confirmation."""
    return WEIGHT_TYPE_VALUES.get(weight_type, WEIGHT_TYPE_VALUES["confirmation"])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compress_hits(hits: list[dict], max_keep: int = 5) -> str:
    """Extract top-weighted hits and join as a current model summary.

    Pure function — no DB calls. Returns shorter, denser text than the
    full hit list by keeping only the highest-weight entries.
    """
    sorted_hits = sorted(hits, key=lambda h: h.get("weight", 0.0), reverse=True)
    top = sorted_hits[:max_keep]
    return " | ".join(h["text"] for h in top if h.get("text"))


def become_knowledgeable_about(topic: str, db_url: str | None = None) -> dict:
    """Create or retrieve a PersistentQuestion for the given topic.

    Idempotent — returns existing inquiry if already present. Returns a
    state dict with keys: path, topic, current_model, metadata.
    """
    import psycopg2

    url = db_url or _DEFAULT_DB_URL
    path = _palace_path(topic)
    title = f"Persistent inquiry: {topic}"

    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT path, content, metadata FROM adc.palace WHERE path = %s",
                (path,),
            )
            row = cur.fetchone()
            if row:
                meta = row[2] if isinstance(row[2], dict) else json.loads(row[2])
                log.info("persistent_inquiry: loaded existing inquiry %r", path)
                return {
                    "path": path,
                    "topic": meta.get("topic", topic),
                    "current_model": row[1],
                    "metadata": meta,
                }

            meta: dict = {
                "topic": topic,
                "hits": [],
                "hit_count": 0,
                "last_compressed_at": None,
                "created_at": _now(),
            }
            cur.execute(
                """
                INSERT INTO adc.palace (path, title, content, node_type, metadata, tags)
                VALUES (%s, %s, %s, 'persistent_inquiry', %s::jsonb, %s::jsonb)
                """,
                (path, title, "", json.dumps(meta), json.dumps(["inquiry"])),
            )
        conn.commit()
        log.info("persistent_inquiry: created inquiry %r", path)
        return {"path": path, "topic": topic, "current_model": "", "metadata": meta}
    finally:
        conn.close()


def add_hit(
    topic: str,
    text: str,
    weight_type: str = "confirmation",
    db_url: str | None = None,
) -> None:
    """Append a weighted hit to an existing inquiry.

    Raises ValueError if the inquiry does not exist — call
    become_knowledgeable_about() first.
    """
    import psycopg2

    url = db_url or _DEFAULT_DB_URL
    path = _palace_path(topic)
    weight = _weight_for_type(weight_type)

    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT metadata FROM adc.palace WHERE path = %s FOR UPDATE",
                (path,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(
                    f"No inquiry for topic {topic!r} — "
                    "call become_knowledgeable_about() first"
                )

            meta = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            hit = {
                "text": text,
                "weight": weight,
                "weight_type": weight_type,
                "added_at": _now(),
            }
            meta["hits"].append(hit)
            meta["hit_count"] = len(meta["hits"])

            cur.execute(
                "UPDATE adc.palace SET metadata = %s::jsonb, updated_at = now() WHERE path = %s",
                (json.dumps(meta), path),
            )
        conn.commit()
        log.info(
            "persistent_inquiry: hit added to %r (weight=%.1f type=%s hits=%d)",
            path,
            weight,
            weight_type,
            meta["hit_count"],
        )
    finally:
        conn.close()


def get_inquiry(topic: str, db_url: str | None = None) -> dict | None:
    """Return current inquiry state dict, or None if not found."""
    import psycopg2

    url = db_url or _DEFAULT_DB_URL
    path = _palace_path(topic)

    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT content, metadata FROM adc.palace WHERE path = %s",
                (path,),
            )
            row = cur.fetchone()
            if not row:
                return None
            meta = row[1] if isinstance(row[1], dict) else json.loads(row[1])
            return {
                "path": path,
                "topic": meta.get("topic", topic),
                "current_model": row[0],
                "hits": meta.get("hits", []),
                "hit_count": meta.get("hit_count", 0),
                "last_compressed_at": meta.get("last_compressed_at"),
                "created_at": meta.get("created_at"),
            }
    finally:
        conn.close()


def compress(
    topic: str,
    max_hits_to_keep: int = 5,
    db_url: str | None = None,
) -> str:
    """Compress accumulated hits into a denser current_model.

    Sorts hits by weight (serendipitous > gap_explanation > confirmation),
    keeps the top max_hits_to_keep, joins as the new model. Returns the
    new model text and updates adc.palace.content + metadata.last_compressed_at.

    Raises ValueError if the inquiry does not exist.
    """
    import psycopg2

    url = db_url or _DEFAULT_DB_URL
    path = _palace_path(topic)

    conn = psycopg2.connect(url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT metadata FROM adc.palace WHERE path = %s FOR UPDATE",
                (path,),
            )
            row = cur.fetchone()
            if not row:
                raise ValueError(
                    f"No inquiry for topic {topic!r} — "
                    "call become_knowledgeable_about() first"
                )

            meta = row[0] if isinstance(row[0], dict) else json.loads(row[0])
            hits = meta.get("hits", [])
            new_model = _compress_hits(hits, max_keep=max_hits_to_keep)
            meta["last_compressed_at"] = _now()

            cur.execute(
                """
                UPDATE adc.palace
                SET content = %s, metadata = %s::jsonb, updated_at = now()
                WHERE path = %s
                """,
                (new_model, json.dumps(meta), path),
            )
        conn.commit()
        log.info(
            "persistent_inquiry: compressed %r → %d chars from %d hits",
            path,
            len(new_model),
            len(hits),
        )
        return new_model
    finally:
        conn.close()
