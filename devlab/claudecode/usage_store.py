"""
usage_store — Per-ticket token/usage actuals persistence (devlab.ticket_usage).

Reads sprint_tokens.log entries for a ticket and stores them durably in
Postgres so a future cost estimator has historical actuals to train on.

Table: devlab.ticket_usage (one row per ticket per worker session)
  ticket_id, worker, provider, model, started_at, closed_at,
  input_tokens, cache_write_tokens, cache_read_tokens, output_tokens,
  total_tokens, cost_usd, wall_clock_s

AR-009: logs the interface crossing when usage is attributed.
"""

from __future__ import annotations
from unseen_university._uu_root import uu_home

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_DB_URL_KEYS = ("UU_HOME_DB_URL", "IGOR_HOME_DB_URL")

_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS devlab;"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS devlab.ticket_usage (
    id              SERIAL PRIMARY KEY,
    ticket_id       TEXT NOT NULL,
    worker          TEXT,
    provider        TEXT,
    model           TEXT,
    started_at      TIMESTAMPTZ,
    closed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    input_tokens    BIGINT NOT NULL DEFAULT 0,
    cache_write_tokens  BIGINT NOT NULL DEFAULT 0,
    cache_read_tokens   BIGINT NOT NULL DEFAULT 0,
    output_tokens   BIGINT NOT NULL DEFAULT 0,
    total_tokens    BIGINT NOT NULL DEFAULT 0,
    cost_usd        NUMERIC(10, 6),
    wall_clock_s    INTEGER,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS ticket_usage_ticket_id ON devlab.ticket_usage (ticket_id);",
    "CREATE INDEX IF NOT EXISTS ticket_usage_closed_at ON devlab.ticket_usage (closed_at DESC);",
]

_IGOR_HOME = Path(uu_home())
_SPRINT_TOKENS_LOG = _IGOR_HOME / "claudecode" / "sprint_tokens.log"


def _read_sprint_log_entries(ticket_id: str, log_path: Path | None = None) -> list[dict]:
    """Read all sprint_tokens.log entries for *ticket_id*.

    Returns list of dicts with keys:
      ts, ticket_id, input_tokens, cache_write_tokens, cache_read_tokens,
      output_tokens, model
    """
    path = log_path or _SPRINT_TOKENS_LOG
    entries = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            parts = line.split("|")
            if len(parts) < 7:
                continue
            if parts[1] != ticket_id:
                continue
            try:
                entries.append({
                    "ts": parts[0],
                    "ticket_id": parts[1],
                    "input_tokens": int(parts[2]),
                    "cache_write_tokens": int(parts[3]),
                    "cache_read_tokens": int(parts[4]),
                    "output_tokens": int(parts[5]),
                    "model": parts[6].strip(),
                })
            except (ValueError, IndexError):
                continue
    except (OSError, FileNotFoundError):
        pass
    return entries


def _aggregate_entries(entries: list[dict]) -> dict:
    """Sum across multiple sprint sessions for one ticket."""
    totals = {
        "input_tokens": 0,
        "cache_write_tokens": 0,
        "cache_read_tokens": 0,
        "output_tokens": 0,
        "model": entries[-1]["model"] if entries else "",
        "started_at": entries[0]["ts"] if entries else None,
    }
    for e in entries:
        totals["input_tokens"] += e["input_tokens"]
        totals["cache_write_tokens"] += e["cache_write_tokens"]
        totals["cache_read_tokens"] += e["cache_read_tokens"]
        totals["output_tokens"] += e["output_tokens"]
    totals["total_tokens"] = (
        totals["input_tokens"]
        + totals["cache_write_tokens"]
        + totals["cache_read_tokens"]
        + totals["output_tokens"]
    )
    return totals


class UsageStore:
    """Low-level CRUD for devlab.ticket_usage."""

    def __init__(self, db_url: str | None = None) -> None:
        self._db_url = db_url
        self._tables_ensured = False

    def _get_db_url(self) -> str:
        if self._db_url:
            return self._db_url
        for key in _DB_URL_KEYS:
            val = os.environ.get(key, "")
            if val:
                return val
        raise RuntimeError(
            "No DB URL — set UU_HOME_DB_URL or IGOR_HOME_DB_URL"
        )

    def _connect(self):
        import psycopg2
        return psycopg2.connect(self._get_db_url())

    def ensure_tables(self) -> None:
        if self._tables_ensured:
            return
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(_CREATE_SCHEMA)
                    cur.execute(_CREATE_TABLE)
                    for idx in _CREATE_INDEXES:
                        cur.execute(idx)
            self._tables_ensured = True
            log.info("usage_store: tables ensured (devlab.ticket_usage)")
        finally:
            conn.close()

    def record(
        self,
        ticket_id: str,
        worker: str | None = None,
        cost_usd: float | None = None,
        started_at: str | None = None,
        closed_at: str | None = None,
        log_path: Path | None = None,
    ) -> bool:
        """Read sprint_tokens.log for *ticket_id* and write a usage row.

        Returns True if a row was written, False if no token data exists.
        AR-009: logs the interface crossing at INFO on success.
        """
        self.ensure_tables()
        entries = _read_sprint_log_entries(ticket_id, log_path=log_path)
        if not entries:
            log.info(
                "usage_store: no sprint_tokens entries for %s — skipping record",
                ticket_id,
            )
            return False

        agg = _aggregate_entries(entries)
        provider = "anthropic"
        if "openrouter" in agg.get("model", "").lower():
            provider = "openrouter"

        wall_clock_s: int | None = None
        if started_at and closed_at:
            try:
                def _parse_ts(ts: str) -> datetime:
                    if ts.endswith("Z"):
                        ts = ts[:-1] + "+00:00"
                    return datetime.fromisoformat(ts)
                wall_clock_s = int((_parse_ts(closed_at) - _parse_ts(started_at)).total_seconds())
            except (ValueError, TypeError):
                pass

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO devlab.ticket_usage
                          (ticket_id, worker, provider, model,
                           started_at, closed_at,
                           input_tokens, cache_write_tokens, cache_read_tokens,
                           output_tokens, total_tokens, cost_usd, wall_clock_s)
                        VALUES
                          (%s, %s, %s, %s,
                           %s::timestamptz, %s::timestamptz,
                           %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            ticket_id,
                            worker,
                            provider,
                            agg["model"],
                            started_at,
                            closed_at or datetime.now(timezone.utc).isoformat(),
                            agg["input_tokens"],
                            agg["cache_write_tokens"],
                            agg["cache_read_tokens"],
                            agg["output_tokens"],
                            agg["total_tokens"],
                            round(cost_usd, 6) if cost_usd is not None else None,
                            wall_clock_s,
                        ),
                    )
            log.info(
                "usage_store: recorded usage for %s — "
                "%d total tokens, $%.4f",
                ticket_id,
                agg["total_tokens"],
                cost_usd or 0.0,
            )
        finally:
            conn.close()
        return True

    def get_by_ticket(self, ticket_id: str) -> list[dict]:
        """Return all usage rows for *ticket_id*, most recent first."""
        self.ensure_tables()
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, ticket_id, worker, provider, model,
                               started_at, closed_at,
                               input_tokens, cache_write_tokens, cache_read_tokens,
                               output_tokens, total_tokens, cost_usd, wall_clock_s,
                               recorded_at
                        FROM devlab.ticket_usage
                        WHERE ticket_id = %s
                        ORDER BY closed_at DESC
                        """,
                        (ticket_id,),
                    )
                    rows = cur.fetchall()
            return [
                {
                    "id": r[0],
                    "ticket_id": r[1],
                    "worker": r[2],
                    "provider": r[3],
                    "model": r[4],
                    "started_at": r[5].isoformat() if r[5] else None,
                    "closed_at": r[6].isoformat() if r[6] else None,
                    "input_tokens": r[7],
                    "cache_write_tokens": r[8],
                    "cache_read_tokens": r[9],
                    "output_tokens": r[10],
                    "total_tokens": r[11],
                    "cost_usd": float(r[12]) if r[12] is not None else None,
                    "wall_clock_s": r[13],
                    "recorded_at": r[14].isoformat() if r[14] else None,
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_aggregate(self, limit: int = 50) -> dict:
        """Return aggregate stats across all recent tickets."""
        self.ensure_tables()
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                          COUNT(DISTINCT ticket_id) AS tickets,
                          SUM(total_tokens)         AS total_tokens,
                          SUM(cost_usd)             AS total_cost_usd,
                          AVG(wall_clock_s)         AS avg_wall_clock_s
                        FROM devlab.ticket_usage
                        WHERE closed_at >= now() - INTERVAL '30 days'
                        """
                    )
                    row = cur.fetchone()
            return {
                "tickets_last_30d": row[0] or 0,
                "total_tokens_last_30d": int(row[1] or 0),
                "total_cost_usd_last_30d": float(row[2] or 0.0),
                "avg_wall_clock_s": float(row[3] or 0.0) if row[3] else None,
            }
        finally:
            conn.close()
