"""
dreaming.py — periodic memory maintenance for clan.memories.

Triggered by COA after every IGOR_DREAMING_INTERVAL NE cycles (env var,
default 50). Disabled when IGOR_DREAMING_INTERVAL=0.

LLM synthesis features retired (T-igor-inner-cc-assess):
  - cross-session pattern proposals (_synthesize) — deleted
  - sprint-pattern palace nodes (_synthesize_sprint_pattern) — deleted
  - schema extraction pass (_schema_extraction_pass) — deleted
The dreaming cycle now performs only memory maintenance:
  - stale memory archival (_archive_stale_memories)
  - Hebbian edge strengthening (_strengthen_coactivated_edges, delegated to Librarian)

_add_proposal / _ensure_proposals / _fingerprint remain as infrastructure
for any future non-LLM proposal sources.

D-dreaming-patterns-2026-05-10
"""

from __future__ import annotations

import hashlib
import json
import logging
import os

from ..paths import paths as _paths

log = logging.getLogger(__name__)

_PG_URL = _paths().home_db_url

DREAMING_INTERVAL_DEFAULT: int = 50

_PROPOSALS_DDL = """
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


def _ensure_proposals(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_PROPOSALS_DDL)


def _fingerprint(kind: str, content: str) -> str:
    return hashlib.md5((kind + content[:200]).encode()).hexdigest()


def _add_proposal(
    conn,
    *,
    kind: str,
    content: str,
    source_module: str,
    extra_metadata: dict | None = None,
) -> int:
    fp = _fingerprint(kind, content)
    metadata: dict = {"fingerprint": fp, "source": source_module}
    if extra_metadata:
        metadata.update(extra_metadata)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM instance.proposals WHERE status='pending' "
            "AND metadata->>'fingerprint' = %s",
            (fp,),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE instance.proposals SET occurrence_count = occurrence_count + 1 "
                "WHERE id = %s",
                (row[0],),
            )
            return row[0]
        cur.execute(
            "INSERT INTO instance.proposals (kind, content, metadata, source_module) "
            "VALUES (%s, %s, %s::jsonb, %s) RETURNING id",
            (kind, content, json.dumps(metadata), source_module),
        )
        return cur.fetchone()[0]


_DECAY_DAYS = lambda: int(os.getenv("IGOR_MEMORY_DECAY_DAYS", "90"))
_DECAY_SCORE_THRESHOLD = lambda: float(
    os.getenv("IGOR_MEMORY_DECAY_SCORE_THRESHOLD", "0.1")
)
_DECAY_EXEMPT_TYPES = ("PROCEDURAL",)


def _archive_stale_memories(conn) -> int:
    """Mark stale low-activation memories as archived in metadata.

    Non-destructive: sets metadata.archived=true (Discworld: repair, don't discard).
    PROCEDURAL memories (habits) are always exempt.
    Returns count of archived memories.
    """
    try:
        decay_days = _DECAY_DAYS()
        threshold = _DECAY_SCORE_THRESHOLD()
        exempt = list(_DECAY_EXEMPT_TYPES)
        placeholders = ",".join(["%s"] * len(exempt))
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT id FROM clan.memories "
                f"WHERE last_activated_at < NOW() - INTERVAL '{decay_days} days' "
                f"  AND (metadata->>'activation_score')::float < %s "
                f"  AND memory_type NOT IN ({placeholders}) "
                f"  AND (metadata->>'archived') IS DISTINCT FROM 'true' "
                f"LIMIT 500",
                (threshold, *exempt),
            )
            stale_ids = [r[0] for r in cur.fetchall()]
        if not stale_ids:
            return 0
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE clan.memories SET metadata = jsonb_set(metadata, '{archived}', 'true') "
                "WHERE id = ANY(%s)",
                (stale_ids,),
            )
        log.info(
            "dreaming: archived %d stale memories (age>%dd, score<%.2f)",
            len(stale_ids),
            decay_days,
            threshold,
        )
        return len(stale_ids)
    except Exception as _e:
        log.warning("_archive_stale_memories failed (non-fatal): %s", _e)
        return 0


_HEBBIAN_THRESHOLD_DEFAULT = 3
_HEBBIAN_DELTA_DEFAULT = 0.1
_HEBBIAN_LOOKBACK_DEFAULT = 100


def _strengthen_coactivated_edges(conn) -> int:
    """Hebbian edge strengthening — delegated to Librarian edge_maintenance.

    Logic moved to devices/librarian/edge_maintenance.py so consolidation
    runs on Librarian's schedule, independent of Igor's dreaming pass.
    This stub delegates to the Librarian service and remains for backwards
    compatibility with existing dreaming.run() callers.
    """
    try:
        from unseen_university.devices.librarian.edge_maintenance import strengthen_coactivated_edges

        return strengthen_coactivated_edges(conn)
    except ImportError:
        log.warning(
            "_strengthen_coactivated_edges: Librarian edge_maintenance unavailable"
        )
        return 0
    except Exception as _e:
        log.warning("_strengthen_coactivated_edges failed (non-fatal): %s", _e)
        return 0


def run(paths_obj=None) -> int:
    """Run one dreaming cycle. Returns number of proposals written (always 0 now).

    LLM synthesis retired (T-igor-inner-cc-assess). This cycle now performs
    only memory maintenance: stale archival + Hebbian edge strengthening.
    Disabled when IGOR_DREAMING_INTERVAL=0.
    """
    interval = int(os.getenv("IGOR_DREAMING_INTERVAL", str(DREAMING_INTERVAL_DEFAULT)))
    if interval == 0:
        return 0

    try:
        conn = _conn()
        try:
            # Stale memory archival (T-igor-memory-decay-dreaming)
            _archive_stale_memories(conn)
            # Hebbian edge strengthening (T-dreaming-wg-hebbian)
            _strengthen_coactivated_edges(conn)
        finally:
            conn.close()
    except Exception as e:
        log.debug("dreaming: maintenance cycle failed: %s", e)

    return 0
