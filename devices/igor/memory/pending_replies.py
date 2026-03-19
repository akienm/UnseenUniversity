"""
pending_replies.py — Resilience queue for writes that couldn't reach home DB (D126 Step 3).

When a write to the Postgres home DB fails (network, failover), the operation is
enqueued here in the LOCAL database. On the next access, drain() retries the
pending writes. After 3 failed attempts, a Worry signal is raised via the
on_worry callback.

Worry (Step 4): a new TWM signal class — internal uncertainty about Igor's own
actions. Raised when a write has been attempted 3+ times without confirmation.
High urgency, high attractor_weight — Igor knows something is unconfirmed and
should acknowledge it before acting on stale data.

Schema (LOCAL table: pending_replies):
    id            SERIAL PK
    table_name    TEXT — target table in home DB
    op            TEXT — 'upsert' | 'insert' | 'update'
    payload_json  TEXT — JSON payload for the operation
    attempt_count INTEGER — how many times we've tried
    last_attempt  TEXT — ISO timestamp of last attempt
    resolved      INTEGER — 1 = confirmed written to home
    created_at    TEXT

Usage:
    store = PendingReplyStore(local_proxy, home_proxy, on_worry=cortex.worry_push)
    store.enqueue("wg_cooccur", "upsert", {"pairs": [...]})
    store.drain()   # call periodically or on next access
"""

from __future__ import annotations

import json
import logging
import time
from typing import Callable, Optional

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_replies (
    id            SERIAL PRIMARY KEY,
    table_name    TEXT NOT NULL,
    op            TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    attempt_count INTEGER DEFAULT 0,
    last_attempt  TEXT,
    resolved      INTEGER DEFAULT 0,
    created_at    TEXT NOT NULL
)
"""

# INTEGER PRIMARY KEY for SQLite compat (SERIAL for PG)
_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS pending_replies (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name    TEXT NOT NULL,
    op            TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    attempt_count INTEGER DEFAULT 0,
    last_attempt  TEXT,
    resolved      INTEGER DEFAULT 0,
    created_at    TEXT NOT NULL
)
"""

_WORRY_THRESHOLD = 3  # raise Worry after this many failed attempts


class PendingReplyStore:
    """
    Write-ahead queue for home DB operations that failed due to connectivity.

    Thread-safe. Non-fatal throughout — pending_replies must never crash callers.
    """

    def __init__(
        self,
        local_proxy,  # make_local_proxy() — where the queue lives
        home_proxy,  # make_home_proxy() — where we retry to
        on_worry: Optional[Callable[[str], None]] = None,
    ) -> None:
        self._local = local_proxy
        self._home = home_proxy
        self._on_worry = on_worry  # called with reason string when Worry fires
        self._schema_ready = False
        self._ensure_schema()

    # ── Schema ────────────────────────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        try:
            # Try SERIAL (Postgres) first; fall back to AUTOINCREMENT (SQLite)
            try:
                with self._local() as conn:
                    conn.execute(_SCHEMA)
                self._schema_ready = True
            except Exception:
                with self._local() as conn:
                    conn.execute(_SCHEMA_SQLITE)
                self._schema_ready = True
        except Exception as e:
            log.warning(f"[pending_replies] schema init failed: {e}")

    # ── Enqueue ───────────────────────────────────────────────────────────────

    def enqueue(self, table: str, op: str, payload: dict) -> Optional[int]:
        """
        Enqueue a failed write for later retry.
        Returns the row id or None on failure.
        """
        self._ensure_schema()
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            with self._local() as conn:
                cur = conn.execute(
                    "INSERT INTO pending_replies "
                    "(table_name, op, payload_json, created_at) VALUES (?, ?, ?, ?)",
                    (table, op, json.dumps(payload), now),
                )
                row_id = cur.lastrowid
            log.info(f"[pending_replies] queued op={op} table={table} id={row_id}")
            return row_id
        except Exception as e:
            log.warning(f"[pending_replies] enqueue failed: {e}")
            return None

    # ── Drain ─────────────────────────────────────────────────────────────────

    def drain(self) -> dict:
        """
        Retry all unresolved pending writes against home DB.
        Call on startup or periodically.

        Returns {"attempted": N, "succeeded": M, "failed": K, "worried": W}.
        """
        self._ensure_schema()
        attempted = 0
        succeeded = 0
        failed = 0
        worried = 0
        now = time.strftime("%Y-%m-%dT%H:%M:%S")

        try:
            with self._local() as conn:
                rows = conn.execute(
                    "SELECT id, table_name, op, payload_json, attempt_count "
                    "FROM pending_replies WHERE resolved = 0 "
                    "ORDER BY id ASC LIMIT 100"
                ).fetchall()
        except Exception as e:
            log.warning(f"[pending_replies] drain fetch failed: {e}")
            return {"attempted": 0, "succeeded": 0, "failed": 0, "worried": 0}

        for row in rows:
            attempted += 1
            row_id = row[0]
            table = row[1]
            op = row[2]
            payload = json.loads(row[3])
            attempt_count = row[4]

            ok = self._apply(table, op, payload)
            new_count = attempt_count + 1

            if ok:
                self._mark_resolved(row_id)
                succeeded += 1
                log.info(f"[pending_replies] resolved id={row_id} table={table}")
            else:
                self._increment_attempt(row_id, new_count, now)
                failed += 1
                if new_count >= _WORRY_THRESHOLD:
                    reason = (
                        f"pending_replies: {new_count} failed attempts "
                        f"on table={table} op={op} id={row_id}"
                    )
                    self._raise_worry(reason)
                    worried += 1

        return {
            "attempted": attempted,
            "succeeded": succeeded,
            "failed": failed,
            "worried": worried,
        }

    def _apply(self, table: str, op: str, payload: dict) -> bool:
        """
        Apply one pending operation to the home DB.
        Returns True on success, False on any error.
        """
        try:
            if table == "wg_cooccur" and op == "upsert":
                pairs = payload.get("pairs", [])
                if pairs:
                    with self._home() as conn:
                        conn.executemany(
                            "INSERT INTO wg_cooccur (word_a, word_b, score) VALUES (?, ?, ?) "
                            "ON CONFLICT(word_a, word_b) "
                            "DO UPDATE SET score = wg_cooccur.score + excluded.score",
                            pairs,
                        )
                return True
            else:
                # Generic JSON upsert path for other tables
                cols = [k for k in payload if k != "pairs"]
                if not cols:
                    log.warning(
                        f"[pending_replies] unknown op={op} table={table} — skipping"
                    )
                    return True  # mark resolved to stop retrying unknown ops
                vals = [payload[c] for c in cols]
                col_str = ", ".join(cols)
                ph = ", ".join(["?"] * len(cols))
                with self._home() as conn:
                    conn.execute(
                        f"INSERT INTO {table} ({col_str}) VALUES ({ph}) "
                        f"ON CONFLICT DO NOTHING",
                        vals,
                    )
                return True
        except Exception as e:
            log.warning(f"[pending_replies] apply failed table={table} op={op}: {e}")
            return False

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _mark_resolved(self, row_id: int) -> None:
        try:
            with self._local() as conn:
                conn.execute(
                    "UPDATE pending_replies SET resolved = 1 WHERE id = ?", (row_id,)
                )
        except Exception as e:
            log.warning(f"[pending_replies] mark_resolved failed: {e}")

    def _increment_attempt(self, row_id: int, new_count: int, ts: str) -> None:
        try:
            with self._local() as conn:
                conn.execute(
                    "UPDATE pending_replies SET attempt_count = ?, last_attempt = ? WHERE id = ?",
                    (new_count, ts, row_id),
                )
        except Exception as e:
            log.warning(f"[pending_replies] increment_attempt failed: {e}")

    def _raise_worry(self, reason: str) -> None:
        """Fire the Worry signal via callback if wired."""
        log.warning(f"[pending_replies] WORRY: {reason}")
        if self._on_worry is not None:
            try:
                self._on_worry(reason)
            except Exception as e:
                log.warning(f"[pending_replies] on_worry callback failed: {e}")

    # ── Pending count (for metrics / introspect) ──────────────────────────────

    def pending_count(self) -> int:
        """Return count of unresolved pending writes."""
        try:
            with self._local() as conn:
                return conn.execute(
                    "SELECT COUNT(*) FROM pending_replies WHERE resolved = 0"
                ).fetchone()[0]
        except Exception:
            return 0
