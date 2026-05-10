"""
proposals.py — instance.proposals queue for Igor-approved clan.memories writes.

Dreaming, librarian, and playbook modules PROPOSE via add_proposal().
Igor NE habits COMMIT accepted proposals to clan.memories via commit_proposal().
This enforces the "Igor decides what goes into clan.memories" principle.

D-activate-primitive-2026-05-10
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_PG_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS instance.proposals (
    id                  serial PRIMARY KEY,
    kind                text NOT NULL,
    content             text NOT NULL,
    metadata            jsonb NOT NULL DEFAULT '{}',
    status              text NOT NULL DEFAULT 'pending',
    source_module       text,
    occurrence_count    int NOT NULL DEFAULT 1,
    first_seen_at       timestamptz NOT NULL DEFAULT now(),
    created_at          timestamptz NOT NULL DEFAULT now(),
    committed_at        timestamptz,
    committed_memory_id bigint,
    rejected_at         timestamptz,
    rejected_reason     text,
    CONSTRAINT proposals_status_check CHECK (status IN ('pending', 'committed', 'rejected'))
)
"""


def _conn():
    import psycopg2

    return psycopg2.connect(_PG_URL)


def _ensure_table() -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE_TABLE)
    finally:
        conn.close()


def _content_fingerprint(kind: str, content: str) -> str:
    return hashlib.md5((kind + content[:200]).encode()).hexdigest()


def add_proposal(
    kind: str,
    content: str,
    metadata: dict | None = None,
    source_module: str | None = None,
) -> int:
    """Add a proposal or increment occurrence_count if a matching pending one exists.

    Returns the proposal id (new or existing).
    kind: 'habit' | 'watch_q' | 'playbook' | 'archive_action'
    """
    _ensure_table()
    fingerprint = _content_fingerprint(kind, content)
    md = metadata or {}
    md["fingerprint"] = fingerprint

    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                # Check for existing pending proposal with same fingerprint
                cur.execute(
                    """SELECT id FROM instance.proposals
                       WHERE status = 'pending'
                         AND metadata->>'fingerprint' = %s""",
                    (fingerprint,),
                )
                row = cur.fetchone()
                if row:
                    existing_id = row[0]
                    cur.execute(
                        """UPDATE instance.proposals
                           SET occurrence_count = occurrence_count + 1
                           WHERE id = %s""",
                        (existing_id,),
                    )
                    return existing_id

                cur.execute(
                    """INSERT INTO instance.proposals
                       (kind, content, metadata, source_module)
                       VALUES (%s, %s, %s::jsonb, %s)
                       RETURNING id""",
                    (kind, content, json.dumps(md), source_module),
                )
                return cur.fetchone()[0]
    finally:
        conn.close()


def read_pending(limit: int = 50) -> list[dict]:
    """Return pending proposals, newest first."""
    _ensure_table()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, kind, content, metadata, source_module,
                          occurrence_count, first_seen_at, created_at
                   FROM instance.proposals
                   WHERE status = 'pending'
                   ORDER BY occurrence_count DESC, created_at DESC
                   LIMIT %s""",
                (limit,),
            )
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def commit_proposal(proposal_id: int, memory_id: int | None = None) -> None:
    """Mark a proposal committed, optionally linking to the resulting clan.memories row."""
    conn = _conn()
    now = datetime.now(timezone.utc)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE instance.proposals
                       SET status = 'committed', committed_at = %s, committed_memory_id = %s
                       WHERE id = %s""",
                    (now, memory_id, proposal_id),
                )
    finally:
        conn.close()


def reject_proposal(proposal_id: int, reason: str) -> None:
    """Mark a proposal rejected with a reason."""
    conn = _conn()
    now = datetime.now(timezone.utc)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE instance.proposals
                       SET status = 'rejected', rejected_at = %s, rejected_reason = %s
                       WHERE id = %s""",
                    (now, reason, proposal_id),
                )
    finally:
        conn.close()
