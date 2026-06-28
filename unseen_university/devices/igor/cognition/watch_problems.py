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
import time
import uuid
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras

from ..paths import paths as _paths

log = logging.getLogger(__name__)

_TABLE = "watch_problems"

# Minimum interval between lever_watcher scans — prevents flooding when NE
# cycles fast on empty TWM (each NE cycle calls lever_watcher).
_LEVER_WATCHER_MIN_INTERVAL_SEC = float(
    os.environ.get("IGOR_LEVER_WATCHER_INTERVAL_SEC", "300")
)
_lever_watcher_last_run: float = 0.0

# Stop words excluded from keyword matching — common English words that appear
# in any TWM content and produce false lever hits.
_LEVER_STOP: frozenset[str] = frozenset(
    {
        "recent",
        "recently",
        "surfaced",
        "problem",
        "found",
        "possible",
        "studies",
        "observations",
        "support",
        "continue",
        "information",
        "this",
        "that",
        "with",
        "from",
        "have",
        "been",
        "were",
        "they",
        "some",
        "more",
        "about",
        "into",
        "just",
        "also",
        "when",
        "then",
        "which",
        "their",
        "there",
        "could",
        "would",
        "should",
        "other",
        "each",
        "time",
        "will",
        "even",
        "over",
        "such",
        "here",
        "well",
        "using",
        "used",
        "being",
        "these",
        "those",
        "make",
        "made",
    }
)

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

_MIGRATE_SQL = """
ALTER TABLE watch_problems ADD COLUMN IF NOT EXISTS confidence_score float NOT NULL DEFAULT 0.0;
ALTER TABLE watch_problems ADD COLUMN IF NOT EXISTS last_evidenced_at timestamptz;
"""

_CONFIDENCE_THRESHOLD_DEFAULT = 0.7
_CONFIDENCE_DECAY_DEFAULT = 0.95


def _conn() -> "psycopg2.connection":
    search_path = os.environ.get("IGOR_LOCAL_SEARCH_PATH", "instance,infra,public")
    return psycopg2.connect(
        _paths().home_db_url, options=f"-c search_path={search_path}"
    )


def _ensure_table(cur) -> None:
    cur.execute(_CREATE_SQL)
    cur.execute(_MIGRATE_SQL)


def add_watch_problem(
    problem: str,
    lever_description: str | None = None,
    watch_condition: str | None = None,
    parent_id: int | None = None,
) -> int:
    """Add an unsolved problem to the per-instance watch list.

    When watch_condition is not None and an unresolved row with that exact
    condition already exists, updates last_surfaced_at on the existing row and
    returns its id — prevents accumulation of duplicate escalation rows.

    Returns the row's integer id (new or existing), or -1 on error.
    """
    try:
        conn = _conn()
        with conn:
            with conn.cursor() as cur:
                _ensure_table(cur)
                if watch_condition is not None:
                    cur.execute(
                        f"SELECT id FROM {_TABLE} "
                        "WHERE watch_condition = %s AND resolved_at IS NULL "
                        "LIMIT 1",
                        (watch_condition,),
                    )
                    existing = cur.fetchone()
                    if existing:
                        existing_id = existing[0]
                        cur.execute(
                            f"UPDATE {_TABLE} SET last_surfaced_at = now() "
                            "WHERE id = %s",
                            (existing_id,),
                        )
                        log.debug(
                            "watch_problems: dedup on condition %r — reusing #%d",
                            watch_condition,
                            existing_id,
                        )
                        return existing_id
                key = str(uuid.uuid4())
                _meta: dict = {}
                if os.environ.get("IGOR_TEST_MODE") == "1":
                    _meta["test_data"] = "true"
                cur.execute(
                    f"INSERT INTO {_TABLE} "
                    "(problem_key, parent_id, problem, lever_description, watch_condition, metadata) "
                    "VALUES (%s, %s, %s, %s, %s, %s::jsonb) RETURNING id",
                    (
                        key,
                        parent_id,
                        problem,
                        lever_description,
                        watch_condition,
                        json.dumps(_meta),
                    ),
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
    Uses keyword overlap as the match heuristic. Accumulates confidence_score
    across cycles; escalates only when confidence crosses IGOR_WATCH_CONFIDENCE_THRESHOLD
    (default 0.7). Applies exponential decay on no-match cycles.

    Returns the count of problems escalated (threshold-crossing) this cycle.
    Self-throttles to at most one full scan per _LEVER_WATCHER_MIN_INTERVAL_SEC
    so NE empty-cycle bursts can't flood the channel.
    """
    global _lever_watcher_last_run
    now_mono = time.monotonic()
    if now_mono - _lever_watcher_last_run < _LEVER_WATCHER_MIN_INTERVAL_SEC:
        return 0
    _lever_watcher_last_run = now_mono

    from .escalate import escalate_to_channel

    threshold = float(
        os.getenv("IGOR_WATCH_CONFIDENCE_THRESHOLD", str(_CONFIDENCE_THRESHOLD_DEFAULT))
    )
    decay = float(
        os.getenv("IGOR_WATCH_CONFIDENCE_DECAY", str(_CONFIDENCE_DECAY_DEFAULT))
    )

    problems = read_active_problems()
    if not problems:
        return 0

    if recent_twm_rows is None:
        recent_twm_rows = _load_recent_twm()

    twm_text = " ".join((r.get("content_csb") or "") for r in recent_twm_rows).lower()

    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)

    # First pass: compute all updates (skip problems with no condition)
    results = []  # (prob, new_confidence, had_match, matches)
    for prob in problems:
        condition = (prob.get("watch_condition") or "").lower()
        if not condition:
            continue

        keywords = [w for w in condition.split() if len(w) > 4 and w not in _LEVER_STOP]
        matches = [kw for kw in keywords if kw in twm_text]
        had_match = len(matches) >= 2

        old_confidence = float(prob.get("confidence_score") or 0.0)
        if had_match:
            new_confidence = min(old_confidence + 0.1, 1.0)
        else:
            new_confidence = old_confidence * decay
            if new_confidence < 0.01:
                new_confidence = 0.0

        results.append((prob, new_confidence, had_match, matches))

    # Second pass: batch all confidence UPDATEs in a single connection
    try:
        conn = _conn()
        with conn:
            with conn.cursor() as cur:
                for prob, new_confidence, had_match, _ in results:
                    if had_match:
                        cur.execute(
                            f"UPDATE {_TABLE} SET confidence_score = %s, "
                            "last_evidenced_at = now() WHERE id = %s",
                            (new_confidence, prob["id"]),
                        )
                    else:
                        cur.execute(
                            f"UPDATE {_TABLE} SET confidence_score = %s WHERE id = %s",
                            (new_confidence, prob["id"]),
                        )
        conn.close()
    except Exception as _e:
        log.warning("watch_problems.lever_watcher confidence update failed: %s", _e)

    # Third pass: escalations for threshold-crossing problems
    surfaced = 0
    for prob, new_confidence, had_match, matches in results:
        if not had_match or new_confidence < threshold:
            continue

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

        if not matches:
            continue

        problem_text = prob.get("problem", "")[:80]
        snippet = _find_snippet(twm_text, matches[0])
        escalate_to_channel(
            f"[Watch list — elevated confidence={new_confidence:.2f}] "
            f"possible lever found: {problem_text} "
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
                    "SELECT content_csb FROM twm_observations "
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
