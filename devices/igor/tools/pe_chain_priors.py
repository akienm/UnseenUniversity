"""
pe_chain_priors.py — Persistent negative priors for the HYPOTHESIZE step.

Records files that triggered SCOPE_GUARD escalations or old_string validation
failures. Injects top-N offenders into the HYPOTHESIZE prompt so the model
stops regenerating known-bad edit targets.

Table: instance.pe_chain_priors
  - Upsert semantics on (target_path, symbol, kind)
  - count increments on each trip

API:
  append_prior(target_path, symbol, kind)   — write/increment a prior
  get_top_priors(n=5)                       — top-N rows by count DESC
  build_priors_prompt_block(n=5)            — formatted block for HYPOTHESIZE injection
"""

from __future__ import annotations

import logging
import os

from ..paths import paths as _paths

log = logging.getLogger(__name__)

_PG_URL = _paths().home_db_url

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS instance.pe_chain_priors (
    id          serial PRIMARY KEY,
    target_path text NOT NULL,
    symbol      text NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('scope_guard', 'old_string_mismatch')),
    count       int NOT NULL DEFAULT 1,
    last_seen   timestamptz NOT NULL DEFAULT now(),
    created_at  timestamptz NOT NULL DEFAULT now()
);
"""

_CREATE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS pe_chain_priors_uq
    ON instance.pe_chain_priors (target_path, symbol, kind);
"""


def _conn():
    import psycopg2

    return psycopg2.connect(_PG_URL)


def _ensure_table() -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(_CREATE_SQL)
                cur.execute(_CREATE_INDEX_SQL)
    finally:
        conn.close()


def append_prior(target_path: str, symbol: str, kind: str) -> None:
    """Upsert a prior — increments count if (target_path, symbol, kind) exists."""
    if not target_path:
        return
    try:
        _ensure_table()
        conn = _conn()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO instance.pe_chain_priors
                            (target_path, symbol, kind)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (target_path, symbol, kind) DO UPDATE
                            SET count    = instance.pe_chain_priors.count + 1,
                                last_seen = now()
                        """,
                        (target_path, symbol, kind),
                    )
        finally:
            conn.close()
        log.debug(
            "pe_chain_priors: recorded %s target=%s symbol=%s",
            kind,
            target_path,
            symbol,
        )
    except Exception as _e:
        log.warning("pe_chain_priors.append_prior failed: %s", _e)


def get_top_priors(n: int = 5) -> list[dict]:
    """Return top-N priors by count descending. Empty list on error."""
    try:
        _ensure_table()
        conn = _conn()
        try:
            import psycopg2.extras

            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT target_path, symbol, kind, count
                    FROM instance.pe_chain_priors
                    ORDER BY count DESC
                    LIMIT %s
                    """,
                    (n,),
                )
                rows = cur.fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as _e:
        log.warning("pe_chain_priors.get_top_priors failed: %s", _e)
        return []


def build_priors_prompt_block(n: int = 5) -> str:
    """
    Return a formatted block listing the top-N known-bad targets for prompt injection.
    Returns empty string when no priors exist or on error.
    """
    priors = get_top_priors(n)
    if not priors:
        return ""

    # Aggregate by target_path so one file shows all its error kinds on one line
    by_path: dict[str, list[str]] = {}
    for p in priors:
        path = p["target_path"]
        tag = f"{p['kind']} ×{p['count']}"
        by_path.setdefault(path, []).append(tag)

    lines = [
        "KNOWN BAD TARGETS — files that caused errors in recent sprints "
        "(avoid as edit targets unless the ticket explicitly names them):"
    ]
    for path, tags in by_path.items():
        lines.append(f"- {path}: {', '.join(tags)}")

    return "\n".join(lines)
