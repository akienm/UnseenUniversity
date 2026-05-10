"""
dreaming.py — cross-session pattern synthesis for clan.memories.

Triggered by COA after every IGOR_DREAMING_INTERVAL NE cycles (env var,
default 50). Disabled when IGOR_DREAMING_INTERVAL=0.

Read sources:
  - igor_psych.jsonl (last N entries)
  - instance.watch_problems (active, recently-evidenced)

Synthesis: haiku call proposes habit or WATCH_Q additions.
Output: writes to instance.proposals (kind='habit' or kind='watch_q').
Dreaming PROPOSES, Igor DECIDES — no direct clan.memories writes.

D-dreaming-patterns-2026-05-10
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time

log = logging.getLogger(__name__)

_PG_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

DREAMING_INTERVAL_DEFAULT: int = 50
PSYCH_LOG_WINDOW: int = 20
WATCH_PROBLEMS_WINDOW: int = 10

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


def _add_proposal(conn, *, kind: str, content: str, source_module: str) -> int:
    fp = _fingerprint(kind, content)
    metadata = {"fingerprint": fp, "source": source_module}
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


def _read_psych_log(paths_obj) -> list[dict]:
    """Read the last PSYCH_LOG_WINDOW entries from igor_psych.jsonl."""
    try:
        psych_log = paths_obj.logs / "igor_psych.jsonl"
        if not psych_log.exists():
            return []
        entries = []
        with psych_log.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except Exception:
                        pass
        return entries[-PSYCH_LOG_WINDOW:]
    except Exception as e:
        log.debug("dreaming: psych_log read failed: %s", e)
        return []


def _read_watch_problems() -> list[dict]:
    """Read recently-evidenced active watch_problems."""
    try:
        conn = _conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT problem, watch_condition, confidence_score
                    FROM instance.watch_problems
                    WHERE resolved_at IS NULL
                      AND confidence_score > 0.1
                    ORDER BY confidence_score DESC
                    LIMIT %s
                    """,
                    (WATCH_PROBLEMS_WINDOW,),
                )
                return [
                    {
                        "problem": r[0],
                        "watch_condition": r[1],
                        "confidence": float(r[2]),
                    }
                    for r in cur.fetchall()
                ]
        finally:
            conn.close()
    except Exception as e:
        log.debug("dreaming: watch_problems read failed: %s", e)
        return []


def _synthesize(psych_entries: list[dict], watch_problems: list[dict]) -> list[dict]:
    """Call haiku to synthesize cross-session patterns. Returns list of proposals."""
    try:
        from ..tools.inner_cc import call_inner_cc_long

        psych_summary = "\n".join(
            f"- ts={e.get('ts', 0):.0f} valence={e.get('valence', 0):.2f} "
            f"arousal={e.get('arousal', 0):.2f} notes={str(e.get('notes', ''))[:80]}"
            for e in psych_entries[-10:]
        )
        watch_summary = "\n".join(
            f"- [{w['confidence']:.2f}] {w['problem'][:80]}" for w in watch_problems
        )

        prompt = f"""You are analyzing an AI agent's cognitive patterns across recent sessions.

Recent psychological log entries (last 10 cycles):
{psych_summary or "(none)"}

Active watch problems (by confidence):
{watch_summary or "(none)"}

Identify 0-3 recurring patterns that would benefit from:
- A new procedural habit (kind=habit): a reusable response pattern
- A new watch question (kind=watch_q): something worth watching for

Respond ONLY with valid JSON array (may be empty):
[
  {{
    "kind": "habit" or "watch_q",
    "content": "<narrative description of the habit or watch question>",
    "rationale": "<one sentence: why this pattern warrants a new entry>"
  }}
]"""

        raw = call_inner_cc_long(task=prompt, model="anthropic/claude-haiku-4-5")
        answer = (raw.get("answer") or "").strip()
        if answer.startswith("```"):
            parts = answer.split("```")
            answer = parts[1] if len(parts) > 1 else ""
            if answer.startswith("json"):
                answer = answer[4:]
        proposals = json.loads(answer)
        if not isinstance(proposals, list):
            return []
        return [
            p
            for p in proposals
            if isinstance(p, dict)
            and p.get("kind") in ("habit", "watch_q")
            and p.get("content")
        ]
    except Exception as e:
        log.warning("dreaming: synthesis failed: %s", e)
        return []


def run(paths_obj=None) -> int:
    """Run one dreaming cycle. Returns number of proposals written.

    paths_obj: Igor paths() object for locating psych_log. If None, imports paths().
    Disabled when IGOR_DREAMING_INTERVAL=0.
    """
    interval = int(os.getenv("IGOR_DREAMING_INTERVAL", str(DREAMING_INTERVAL_DEFAULT)))
    if interval == 0:
        return 0

    try:
        if paths_obj is None:
            from ..paths import paths as _paths

            paths_obj = _paths()
    except Exception as e:
        log.debug("dreaming: paths() failed: %s", e)
        return 0

    psych_entries = _read_psych_log(paths_obj)
    watch_problems = _read_watch_problems()

    if not psych_entries and not watch_problems:
        log.debug("dreaming: nothing to synthesize (empty inputs)")
        return 0

    proposals = _synthesize(psych_entries, watch_problems)
    if not proposals:
        return 0

    conn = _conn()
    try:
        with conn:
            _ensure_proposals(conn)
        count = 0
        with conn:
            for p in proposals:
                try:
                    _add_proposal(
                        conn,
                        kind=p["kind"],
                        content=p["content"],
                        source_module="dreaming",
                    )
                    count += 1
                except Exception as e:
                    log.warning("dreaming: add_proposal failed: %s", e)
        log.info(
            "dreaming: wrote %d proposals from %d candidates", count, len(proposals)
        )
        return count
    finally:
        conn.close()
