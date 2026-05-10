"""
Per-instance watch problems — the instance.* tier of Igor's watch list.

The watch list has two tiers:
  clan.memories  WATCH_Q_*/WATCH_T_* — universal questions and topic interests
                 shared across all Igor instances (already exists)
  instance.watch_problems (this module) — unsolved problems specific to this
                 Igor instance; written when escalation fires, scanned by
                 lever_watcher() on each NE cycle

D-escalate-as-default-2026-05-10: grand escalation = park the stuck problem
here with a structured lever description, then watch for incoming information
that might unlock it.

Tree structure: parent_id allows sub-problems to hang under a root problem,
mirroring the clan.memories WATCH_Q_*/WATCH_T_* tree organisation.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras

log = logging.getLogger(__name__)

_TABLE = "instance.watch_problems"
_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    id               SERIAL PRIMARY KEY,
    problem_key      TEXT UNIQUE NOT NULL,
    parent_id        INTEGER REFERENCES {_TABLE}(id),
    problem          TEXT NOT NULL,
    lever_description TEXT,
    watch_condition  TEXT,
    created_at       TIMESTAMPTZ DEFAULT now(),
    resolved_at      TIMESTAMPTZ,
    last_surfaced_at TIMESTAMPTZ,
    metadata         JSONB DEFAULT '{{}}'::jsonb
)
"""


def _conn() -> "psycopg2.connection":
    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    return psycopg2.connect(db_url)


def _ensure_table(cur) -> None:
    cur.execute(_CREATE_SQL)


def add_watch_problem(
    problem: str,
    lever_description: str | None = None,
    watch_condition: str | None = None,
    parent_id: int | None = None,
) -> int:
    """Add an unsolved problem to the per-instance watch list.

    Returns the new row's integer id.
    """
    key = str(uuid.uuid4())
    try:
        conn = _conn()
        with conn:
            with conn.cursor() as cur:
                _ensure_table(cur)
                cur.execute(
                    f"INSERT INTO {_TABLE} "
                    "(problem_key, parent_id, problem, lever_description, watch_condition) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (key, parent_id, problem, lever_description, watch_condition),
                )
                row_id = cur.fetchone()[0]
        conn.close()
        log.info("watch_problems: added #%d — %s", row_id, problem[:60])
        return row_id
    except Exception as e:
        log.warning("watch_problems.add_watch_problem failed: %s", e)
        return -1


def read_active_problems() -> list[dict]:
    """Return unresolved watch problems for the lever-watcher scan."""
    try:
        conn = _conn()
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                _ensure_table(cur)
                cur.execute(
                    f"SELECT * FROM {_TABLE} WHERE resolved_at IS NULL ORDER BY id"
                )
                rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        log.warning("watch_problems.read_active_problems failed: %s", e)
        return []


def resolve_problem(problem_id: int) -> None:
    """Mark a watch problem as resolved."""
    try:
        conn = _conn()
        with conn:
            with conn.cursor() as cur:
                _ensure_table(cur)
                cur.execute(
                    f"UPDATE {_TABLE} SET resolved_at = now() WHERE id = %s",
                    (problem_id,),
                )
        conn.close()
        log.info("watch_problems: resolved #%d", problem_id)
    except Exception as e:
        log.warning("watch_problems.resolve_problem failed: %s", e)


def mark_surfaced(problem_id: int) -> None:
    """Record that this problem was surfaced to channel (dedup gate)."""
    try:
        conn = _conn()
        with conn:
            with conn.cursor() as cur:
                _ensure_table(cur)
                cur.execute(
                    f"UPDATE {_TABLE} SET last_surfaced_at = now() WHERE id = %s",
                    (problem_id,),
                )
        conn.close()
    except Exception as e:
        log.warning("watch_problems.mark_surfaced failed: %s", e)


def lever_watcher(recent_twm_rows: list[dict] | None = None) -> int:
    """Scan active watch problems against recent TWM content for lever matches.

    Loads recent instance.twm_observations if recent_twm_rows is not supplied.
    Uses WATCH_Q_07 'Where is the lever?' and keyword overlap as the match
    heuristic. Posts to channel on match; respects a 24-hour dedup gate.

    Returns the count of problems surfaced this cycle.
    """
    from .escalate import escalate_to_channel

    problems = read_active_problems()
    if not problems:
        return 0

    # Load recent TWM if not supplied
    if recent_twm_rows is None:
        recent_twm_rows = _load_recent_twm()

    twm_text = " ".join((r.get("content_csb") or "") for r in recent_twm_rows).lower()

    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    surfaced = 0

    for prob in problems:
        # Skip if surfaced within 24h
        last = prob.get("last_surfaced_at")
        if last:
            if isinstance(last, str):
                try:
                    last = datetime.fromisoformat(last.replace("Z", "+00:00"))
                except ValueError:
                    last = None
            if last and last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if last and last > cutoff_24h:
                continue

        condition = (prob.get("watch_condition") or "").lower()
        if not condition:
            continue

        keywords = [w for w in condition.split() if len(w) > 3]
        matches = [kw for kw in keywords if kw in twm_text]
        if len(matches) < 2:
            continue

        problem_text = prob.get("problem", "")[:80]
        snippet = _find_snippet(twm_text, matches[0])
        escalate_to_channel(
            f"[Watch list] possible lever found for: {problem_text} "
            f"— matched '{matches[0]}' in TWM: {snippet}",
            dedup_key=f"watch-lever-{prob['id']}",
        )
        mark_surfaced(prob["id"])
        surfaced += 1

    return surfaced


def _load_recent_twm(limit: int = 20) -> list[dict]:
    try:
        conn = _conn()
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT content_csb FROM instance.twm_observations "
                    "ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
                rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception as e:
        log.debug("watch_problems._load_recent_twm failed: %s", e)
        return []


def _find_snippet(text: str, keyword: str, window: int = 60) -> str:
    idx = text.find(keyword)
    if idx == -1:
        return ""
    start = max(0, idx - 20)
    end = min(len(text), idx + window)
    return text[start:end].strip()
