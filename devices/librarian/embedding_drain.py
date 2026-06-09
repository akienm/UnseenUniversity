"""
embedding_drain.py — Drain the clan.embedding_queue and write payloads embeddings.

Picks up pending rows from clan.embedding_queue (populated by Postgres trigger
trg_queue_memory_embedding on clan.memories INSERT), computes embeddings via
the Scraps embedding engine, and stores them in clan.memories.payloads so
recall.py's vector search can find them.

Call run_once() from a periodic job (Nanny Ogg cron or Librarian run loop).

D-semantic-indexing-2026-06-09
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

import psycopg2

_log = logging.getLogger(__name__)

_BATCH = 50
_TRUNCATE = 2000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_url: str):
    return psycopg2.connect(db_url)


def run_once(db_url: str | None = None, *, force_fallback: bool = False) -> dict:
    """Process up to _BATCH pending queue entries. Returns stats dict."""
    db_url = db_url or os.environ.get(
        "IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
    )
    try:
        from devices.scraps.embedding_engine import embed
    except ImportError:
        from scraps.embedding_engine import embed  # type: ignore[import]

    stats = {"processed": 0, "errors": 0, "skipped": 0}

    # Fetch batch with a dedicated connection — embed() may open its own DB connections
    conn = _connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT q.memory_id, m.narrative
                FROM clan.embedding_queue q
                JOIN clan.memories m ON m.id = q.memory_id
                WHERE q.status = 'pending'
                  AND m.narrative IS NOT NULL AND m.narrative != ''
                ORDER BY q.queued_at
                LIMIT %s
                """,
                (_BATCH,),
            )
            rows = cur.fetchall()
        conn.commit()
    finally:
        conn.close()

    for memory_id, narrative in rows:
        text = narrative[:_TRUNCATE]
        wconn = _connect(db_url)
        try:
            result = embed(text, force_fallback=force_fallback)
            payload_embedding = json.dumps(
                {"vector": result["vector"], "model": result["model"],
                 "dimension": result["dimension"], "computed_at": _now()}
            )
            with wconn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE clan.memories
                    SET payloads = jsonb_set(
                        coalesce(payloads, '{}'),
                        '{embedding}',
                        %s::jsonb
                    )
                    WHERE id = %s
                    """,
                    (payload_embedding, memory_id),
                )
                cur.execute(
                    "UPDATE clan.embedding_queue SET status = 'done' WHERE memory_id = %s",
                    (memory_id,),
                )
            wconn.commit()
            stats["processed"] += 1
            _log.debug("EMBED_DONE memory_id=%s model=%s", memory_id, result["model"])
        except Exception as exc:
            _log.warning("EMBED_ERROR memory_id=%s error=%s", memory_id, exc)
            wconn.rollback()
            try:
                with wconn.cursor() as cur:
                    cur.execute(
                        "UPDATE clan.embedding_queue SET status = 'error', error_msg = %s"
                        " WHERE memory_id = %s",
                        (str(exc)[:500], memory_id),
                    )
                wconn.commit()
            except Exception:
                pass
            stats["errors"] += 1
        finally:
            wconn.close()

    _log.info(
        "EMBED_DRAIN_DONE processed=%d errors=%d skipped=%d",
        stats["processed"], stats["errors"], stats["skipped"],
    )
    return stats


def queue_depth(db_url: str | None = None) -> int:
    """Return count of pending queue entries."""
    db_url = db_url or os.environ.get(
        "IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
    )
    conn = _connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM clan.embedding_queue WHERE status = 'pending'")
            return cur.fetchone()[0]
    finally:
        conn.close()
