"""
build_digester.digest_store — Postgres persistence for devlab.build_digest.

Stores a compact, ticket-keyed build digest: flat event timeline + status.
Degrades gracefully to flat timeline when structured boundary markers are absent.

Table created on first use (idempotent CREATE TABLE IF NOT EXISTS).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger(__name__)

_DB_URL_KEYS = ("UU_HOME_DB_URL", "UU_HOME_DB_URL")

_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS devlab;"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS devlab.build_digest (
    ticket_id           TEXT PRIMARY KEY,
    status              TEXT,
    started_at          TIMESTAMPTZ,
    last_event_at       TIMESTAMPTZ,
    events              JSONB NOT NULL DEFAULT '[]'::jsonb,
    current_blocker     TEXT,
    has_boundary_markers BOOLEAN NOT NULL DEFAULT false,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_CREATE_INDEX = (
    "CREATE INDEX IF NOT EXISTS build_digest_last_event "
    "ON devlab.build_digest (last_event_at DESC);"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DigestStore:
    """Low-level CRUD for devlab.build_digest."""

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
            "No DB URL — set UU_HOME_DB_URL or UU_HOME_DB_URL"
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
                    cur.execute(_CREATE_INDEX)
            self._tables_ensured = True
            log.info("build_digest: tables ensured")
        finally:
            conn.close()

    def upsert_event(self, event: dict) -> None:
        """Append *event* to the ticket's event list; update status and timestamps.

        Creates a digest row if one doesn't exist yet.
        *event* must have keys: ticket_id, ts, action, summary, has_boundary_marker.
        """
        self.ensure_tables()
        ticket_id = event["ticket_id"]
        ts = event.get("ts") or _now()
        action = event.get("action", "")
        has_marker = bool(event.get("has_boundary_marker"))

        new_event = {"ts": ts, "action": action, "summary": event.get("summary", "")}

        # Derive ticket status from action
        status_map = {
            "add": "sprint",
            "setstatus": None,  # use the 'new' value from summary parsing
            "close": "closed",
            "awaiting_validation": "awaiting_validation",
            "hold": "hold",
        }
        new_status: str | None = status_map.get(action)
        if action == "setstatus":
            # summary is "old → new"; extract new
            parts = event.get("summary", "").split(" → ")
            if len(parts) == 2:
                new_status = parts[1].strip()

        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    # Upsert: insert new row or append event to existing
                    cur.execute(
                        """
                        INSERT INTO devlab.build_digest
                            (ticket_id, status, started_at, last_event_at,
                             events, has_boundary_markers, updated_at)
                        VALUES (%s, %s, %s::timestamptz, %s::timestamptz,
                                %s::jsonb, %s, now())
                        ON CONFLICT (ticket_id) DO UPDATE SET
                            events = (
                                devlab.build_digest.events || EXCLUDED.events
                            ),
                            last_event_at = EXCLUDED.last_event_at,
                            has_boundary_markers = (
                                devlab.build_digest.has_boundary_markers
                                OR EXCLUDED.has_boundary_markers
                            ),
                            updated_at = now()
                        """,
                        (
                            ticket_id,
                            new_status,
                            ts,
                            ts,
                            json.dumps([new_event]),
                            has_marker,
                        ),
                    )
                    # Update status separately if we have one
                    if new_status is not None:
                        cur.execute(
                            "UPDATE devlab.build_digest SET status=%s WHERE ticket_id=%s",
                            (new_status, ticket_id),
                        )
        finally:
            conn.close()

    def get_digest(self, ticket_id: str) -> dict | None:
        """Return the digest for *ticket_id*, or None if not found."""
        self.ensure_tables()
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT ticket_id, status, started_at, last_event_at,
                               events, current_blocker, has_boundary_markers, updated_at
                        FROM devlab.build_digest
                        WHERE ticket_id = %s
                        """,
                        (ticket_id,),
                    )
                    row = cur.fetchone()
            if row is None:
                return None
            return {
                "ticket_id": row[0],
                "status": row[1],
                "started_at": row[2].isoformat() if row[2] else None,
                "last_event_at": row[3].isoformat() if row[3] else None,
                "events": row[4] if isinstance(row[4], list) else [],
                "current_blocker": row[5],
                "has_boundary_markers": row[6],
                "updated_at": row[7].isoformat() if row[7] else None,
            }
        finally:
            conn.close()

    def list_recent(self, limit: int = 20) -> list[dict]:
        """Return the most recently updated digests (up to *limit*)."""
        self.ensure_tables()
        conn = self._connect()
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT ticket_id, status, last_event_at,
                               has_boundary_markers, updated_at
                        FROM devlab.build_digest
                        ORDER BY updated_at DESC
                        LIMIT %s
                        """,
                        (limit,),
                    )
                    rows = cur.fetchall()
            return [
                {
                    "ticket_id": r[0],
                    "status": r[1],
                    "last_event_at": r[2].isoformat() if r[2] else None,
                    "has_boundary_markers": r[3],
                    "updated_at": r[4].isoformat() if r[4] else None,
                }
                for r in rows
            ]
        finally:
            conn.close()
