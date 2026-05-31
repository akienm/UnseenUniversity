"""Curation tools — memory hygiene analysis and proposal generation.

Reads clan.memories, reasons about redundant/stale/underused content,
writes archive_action proposals to instance.proposals.
Librarian PROPOSES, Igor DECIDES — no direct writes to clan.memories.

All findings logged to datacenter_logs/librarian/curation/curation.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_PG_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)
_LOG_ROOT = Path(os.environ.get("ADC_LOG_ROOT", "datacenter_logs"))
_CURATION_LOG = _LOG_ROOT / "librarian" / "curation" / "curation.jsonl"

_FOCUS_QUALITY_LOG_DDL = """
CREATE TABLE IF NOT EXISTS instance.focus_quality_log (
    id                  serial PRIMARY KEY,
    ne_cycle_ts         timestamptz NOT NULL DEFAULT now(),
    memory_id           text NOT NULL,
    was_loaded          bool NOT NULL DEFAULT false,
    was_used            bool NOT NULL DEFAULT false,
    contribution_score  float NOT NULL DEFAULT 0.0,
    logged_at           timestamptz NOT NULL DEFAULT now()
)
"""

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

SCHEMAS = [
    {
        "name": "librarian_quality_log",
        "description": (
            "Read focus_quality_log stats for a memory. Returns was_loaded count, "
            "was_used count, avg contribution_score, and a prune_candidate flag "
            "(loaded frequently but rarely used)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {
                    "type": "string",
                    "description": "Memory ID to query",
                },
                "days": {
                    "type": "integer",
                    "description": "Lookback window in days (default 30)",
                },
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "librarian_curate",
        "description": (
            "Run memory curation analysis. Flags near-duplicate FACTUAL/PROCEDURAL memories, "
            "stale EPISODIC/EXPERIENTIAL memories (no access in N days), and PROCEDURAL "
            "habits sharing the same code_ref. Writes archive_action proposals to "
            "instance.proposals for Igor to review. Returns a summary of findings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "stale_days": {
                    "type": "integer",
                    "description": "Days with no activation to flag as stale (default 30)",
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, analyse only — do not write proposals (default false)",
                },
            },
            "required": [],
        },
    },
]


def _conn():
    import psycopg2

    return psycopg2.connect(_PG_URL)


def _ensure_focus_quality_log(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_FOCUS_QUALITY_LOG_DDL)


def _ensure_proposals(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_PROPOSALS_DDL)


def _fingerprint(kind: str, content: str) -> str:
    return hashlib.md5((kind + content[:200]).encode()).hexdigest()


def _add_proposal(
    conn, *, kind: str, content: str, metadata: dict, source_module: str
) -> int:
    fp = _fingerprint(kind, content)
    metadata["fingerprint"] = fp
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


def _log_finding(finding: dict) -> None:
    try:
        _CURATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _CURATION_LOG.open("a") as f:
            f.write(
                json.dumps({**finding, "ts": datetime.now(timezone.utc).isoformat()})
                + "\n"
            )
    except Exception:
        pass


def _find_duplicate_narratives(conn) -> list[dict]:
    """FACTUAL/PROCEDURAL pairs with identical narrative md5 (>10 chars)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT a.id, b.id, md5(a.narrative)
            FROM clan.memories a
            JOIN clan.memories b ON md5(a.narrative) = md5(b.narrative)
                AND a.id < b.id
                AND a.memory_type = b.memory_type
            WHERE a.memory_type IN ('FACTUAL', 'PROCEDURAL')
              AND length(a.narrative) > 10
            LIMIT 50
            """)
        return [
            {
                "reason": "duplicate_narrative",
                "target_ids": [r[0], r[1]],
                "narrative_hash": r[2],
                "proposed_action": "archive_one_of_pair",
            }
            for r in cur.fetchall()
        ]


def _find_stale_episodics(conn, days: int = 30) -> list[dict]:
    """EPISODIC/EXPERIENTIAL with no activation_score update in `days` days."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, memory_type,
                   COALESCE(last_activated_at::text, 'never') AS last_act
            FROM clan.memories
            WHERE memory_type IN ('EPISODIC', 'EXPERIENTIAL')
              AND (
                  last_activated_at IS NULL
                  OR last_activated_at < now() - (%s || ' days')::interval
              )
              AND activation_count = 0
            LIMIT 50
            """,
            (str(days),),
        )
        return [
            {
                "reason": "stale_no_activation",
                "target_ids": [r[0]],
                "memory_type": r[1],
                "last_activated_at": r[2],
                "proposed_action": "archive",
            }
            for r in cur.fetchall()
        ]


def _find_duplicate_code_refs(conn) -> list[dict]:
    """PROCEDURAL memories sharing the same code_ref value."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT metadata->>'code_ref' AS code_ref,
                   array_agg(id ORDER BY id) AS ids
            FROM clan.memories
            WHERE memory_type = 'PROCEDURAL'
              AND jsonb_exists(metadata, 'code_ref')
              AND metadata->>'code_ref' IS NOT NULL
            GROUP BY metadata->>'code_ref'
            HAVING count(*) > 1
            LIMIT 20
            """)
        return [
            {
                "reason": "duplicate_code_ref",
                "target_ids": list(r[1]),
                "code_ref": r[0],
                "proposed_action": "merge_or_archive_older",
            }
            for r in cur.fetchall()
        ]


def run_curation(stale_days: int = 30, dry_run: bool = False) -> dict:
    """Run curation analysis. Write archive_action proposals unless dry_run=True.

    Returns: {findings_count, proposals_written, dry_run}
    """
    conn = _conn()
    try:
        with conn:
            _ensure_proposals(conn)

        findings: list[dict] = []
        with conn:
            findings += _find_duplicate_narratives(conn)
            findings += _find_stale_episodics(conn, days=stale_days)
            findings += _find_duplicate_code_refs(conn)

        proposals_written = 0
        if not dry_run:
            with conn:
                for f in findings:
                    content = json.dumps(f)
                    _add_proposal(
                        conn,
                        kind="archive_action",
                        content=content,
                        metadata={"reason": f["reason"]},
                        source_module="librarian_curation",
                    )
                    proposals_written += 1

        for f in findings:
            _log_finding(f)

        summary = {
            "findings_count": len(findings),
            "proposals_written": proposals_written,
            "dry_run": dry_run,
            "breakdown": {
                "duplicate_narratives": sum(
                    1 for f in findings if f["reason"] == "duplicate_narrative"
                ),
                "stale_no_activation": sum(
                    1 for f in findings if f["reason"] == "stale_no_activation"
                ),
                "duplicate_code_refs": sum(
                    1 for f in findings if f["reason"] == "duplicate_code_ref"
                ),
            },
        }
        log.info("curation run: %s", summary)
        return summary
    finally:
        conn.close()


def read_quality_log(memory_id: str, days: int = 30) -> dict:
    """Return contribution stats for memory_id from focus_quality_log.

    Returns: {memory_id, loaded_count, used_count, avg_contribution_score,
              prune_candidate (loaded ≥5 times but used <20% of the time)}
    """
    conn = _conn()
    try:
        with conn:
            _ensure_focus_quality_log(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    COUNT(*) FILTER (WHERE was_loaded)        AS loaded_count,
                    COUNT(*) FILTER (WHERE was_used)          AS used_count,
                    COALESCE(AVG(contribution_score), 0.0)    AS avg_score
                FROM instance.focus_quality_log
                WHERE memory_id = %s
                  AND ne_cycle_ts >= now() - (%s || ' days')::interval
                """,
                (memory_id, str(days)),
            )
            row = cur.fetchone()
        loaded, used, avg_score = row
        prune_candidate = (loaded >= 5) and (used / loaded < 0.2 if loaded else False)
        return {
            "memory_id": memory_id,
            "days": days,
            "loaded_count": loaded,
            "used_count": used,
            "avg_contribution_score": float(avg_score),
            "prune_candidate": prune_candidate,
        }
    finally:
        conn.close()


def dispatch(name: str, args: dict) -> str | None:
    if name == "librarian_quality_log":
        memory_id = args.get("memory_id", "")
        days = int(args.get("days", 30))
        result = read_quality_log(memory_id, days=days)
        return json.dumps(result, indent=2)
    if name == "librarian_curate":
        stale_days = int(args.get("stale_days", 30))
        dry_run = bool(args.get("dry_run", False))
        result = run_curation(stale_days=stale_days, dry_run=dry_run)
        return json.dumps(result, indent=2)
    return None
