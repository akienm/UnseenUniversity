"""
playbook.py — PLAYBOOK memory type CRUD for clan.memories.

A playbook is a structured text artifact: conditions (when to apply) +
heuristics (what to do). Dreaming proposes playbooks via instance.proposals;
the NE/habits decision layer commits accepted ones to clan.memories with
memory_type='PLAYBOOK'.

The NE loads active playbooks at prompt construction time as a capped context
block (budget: 500 tokens).

Direct psycopg2 — no cortex.py touch (avoids HIGH-inertia path).
D-dreaming-patterns-2026-05-10
"""

from __future__ import annotations

import json
import logging
import os
import time

log = logging.getLogger(__name__)

_PG_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

_PLAYBOOK_TYPE = "PLAYBOOK"
_PLAYBOOK_BLOCK_TOKEN_CAP = 500
_CHARS_PER_TOKEN = 4  # rough estimate for cap


def _conn():
    import psycopg2

    return psycopg2.connect(_PG_URL)


def _playbook_id(source: str, ts: float | None = None) -> str:
    """Generate a PLAYBOOK_<timestamp> memory id."""
    ts = ts or time.time()
    return f"PLAYBOOK_{int(ts * 1000) % 10_000_000_000:010d}"


def add_playbook(
    narrative: str,
    *,
    source: str = "dreaming",
    conditions: str = "",
    heuristics: str = "",
) -> str:
    """Insert a new active playbook into clan.memories. Returns memory_id."""
    conn = _conn()
    try:
        mid = _playbook_id(source)
        metadata = {
            "memory_type": _PLAYBOOK_TYPE,
            "active": True,
            "source": source,
            "conditions": conditions,
            "heuristics": heuristics,
        }
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clan.memories (id, memory_type, narrative, metadata) "
                    "VALUES (%s, %s, %s, %s::jsonb) "
                    "ON CONFLICT (id) DO NOTHING",
                    (mid, _PLAYBOOK_TYPE, narrative, json.dumps(metadata)),
                )
        return mid
    finally:
        conn.close()


def read_active_playbooks() -> list[dict]:
    """Return all active playbooks from clan.memories.

    Returns list of {id, narrative, conditions, heuristics, source}.
    """
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, narrative, metadata FROM clan.memories "
                "WHERE memory_type = %s "
                "AND (metadata->>'active')::boolean IS NOT FALSE "
                "ORDER BY id",
                (_PLAYBOOK_TYPE,),
            )
            rows = cur.fetchall()
        result = []
        for mid, narrative, metadata in rows:
            md = metadata if isinstance(metadata, dict) else {}
            result.append(
                {
                    "id": mid,
                    "narrative": narrative or "",
                    "conditions": md.get("conditions", ""),
                    "heuristics": md.get("heuristics", ""),
                    "source": md.get("source", "unknown"),
                }
            )
        return result
    finally:
        conn.close()


def archive_playbook(memory_id: str) -> bool:
    """Set active=false on a playbook. Never hard-deletes. Returns True if found."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE clan.memories "
                    "SET metadata = jsonb_set(COALESCE(metadata, '{}'), '{active}', 'false') "
                    "WHERE id = %s AND memory_type = %s",
                    (memory_id, _PLAYBOOK_TYPE),
                )
                return cur.rowcount > 0
    finally:
        conn.close()


def playbook_context_block() -> str:
    """Return a token-capped string block of active playbooks for NE injection.

    Caps at _PLAYBOOK_BLOCK_TOKEN_CAP tokens (estimated by char count).
    Returns empty string when no active playbooks exist.
    """
    playbooks = read_active_playbooks()
    if not playbooks:
        return ""
    cap_chars = _PLAYBOOK_BLOCK_TOKEN_CAP * _CHARS_PER_TOKEN
    lines = ["PLAYBOOKS (structured heuristics Igor applies):"]
    total = len(lines[0])
    for pb in playbooks:
        entry = f"  [{pb['id']}] {pb['narrative']}"
        if pb.get("conditions"):
            entry += f"\n    When: {pb['conditions']}"
        if pb.get("heuristics"):
            entry += f"\n    How: {pb['heuristics']}"
        if total + len(entry) > cap_chars:
            lines.append("  (additional playbooks omitted — token cap reached)")
            break
        lines.append(entry)
        total += len(entry)
    return "\n".join(lines)
