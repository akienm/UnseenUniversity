"""
PgBus — Postgres-backed message bus.

Drop-in replacement for IMAPServer. Uses:
  - bus.mailboxes table for registered mailbox names
  - bus.messages table (JSONB) for message persistence
  - LISTEN/NOTIFY for push delivery (idle_wait)

Interface is identical to IMAPServer so all callers work unchanged.

One persistent Postgres connection per listener thread is required for
LISTEN/NOTIFY — each call to idle_wait() opens and closes a dedicated
connection. All other operations use short-lived per-call connections.

Usage:
    bus = PgBus()
    bus.start()                      # creates schema; ensures Shared mailbox
    bus.create_mailbox("CC.0")
    bus.append("CC.0", envelope)
    msgs = bus.fetch_unseen("CC.0")  # marks seen
    count = bus.unseen_count("CC.0")
    bus.stop()
"""

from __future__ import annotations

import json
import logging
import os
import re
import select
import time

import psycopg2

from bus.envelope import Envelope

log = logging.getLogger(__name__)

_DEFAULT_DSN = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

_SCHEMA_SQL = """
CREATE SCHEMA IF NOT EXISTS bus;

CREATE TABLE IF NOT EXISTS bus.mailboxes (
    name             TEXT PRIMARY KEY,
    feed_type        TEXT NOT NULL DEFAULT 'personal',
    notify_threshold INT  NOT NULL DEFAULT 5,
    created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bus.messages (
    id            BIGSERIAL PRIMARY KEY,
    mailbox       TEXT NOT NULL,
    from_device   TEXT NOT NULL,
    envelope_json JSONB NOT NULL,
    importance    INT  NOT NULL DEFAULT 3,
    notified      BOOLEAN NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ DEFAULT now(),
    seen          BOOLEAN DEFAULT false
);

CREATE INDEX IF NOT EXISTS bus_messages_mailbox_unseen
    ON bus.messages (mailbox, created_at)
    WHERE NOT seen;
"""

# Idempotent migrations: add columns added after initial schema deploy.
_MIGRATE_SQL = """
ALTER TABLE bus.mailboxes ADD COLUMN IF NOT EXISTS feed_type TEXT NOT NULL DEFAULT 'personal';
ALTER TABLE bus.mailboxes ADD COLUMN IF NOT EXISTS notify_threshold INT NOT NULL DEFAULT 5;
ALTER TABLE bus.messages  ADD COLUMN IF NOT EXISTS importance INT NOT NULL DEFAULT 3;
ALTER TABLE bus.messages  ADD COLUMN IF NOT EXISTS notified BOOLEAN NOT NULL DEFAULT false;
"""

DEBUG_CAP = 1_000


def _channel(mailbox: str) -> str:
    """Sanitize a mailbox name to a valid Postgres NOTIFY channel identifier.

    Postgres folds unquoted identifiers to lowercase, so we lowercase here to
    ensure LISTEN and pg_notify() always target the same channel name.
    """
    return re.sub(r"[^a-z0-9_]", "_", mailbox.lower())


class PgBus:
    """
    Postgres-backed message bus.

    Identical interface to IMAPServer — all callers work unchanged.
    """

    SHARED_MAILBOX = "Shared"

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn or _DEFAULT_DSN

    def _connect(self) -> psycopg2.extensions.connection:
        return psycopg2.connect(self._dsn)

    def start(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA_SQL)
                cur.execute(_MIGRATE_SQL)
        self.create_mailbox(self.SHARED_MAILBOX, feed_type="public")
        log.info("PgBus: started (dsn=%.40s...)", self._dsn)

    def stop(self) -> None:
        log.info("PgBus: stopped")

    # ── Mailbox registry ───────────────────────────────────────────────────────

    def create_mailbox(
        self, name: str, feed_type: str = "personal", notify_threshold: int = 5
    ) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO bus.mailboxes (name, feed_type, notify_threshold)"
                    " VALUES (%s, %s, %s) ON CONFLICT (name) DO NOTHING",
                    (name, feed_type, notify_threshold),
                )
        log.info(
            "PgBus: create_mailbox %r feed_type=%r notify_threshold=%d",
            name, feed_type, notify_threshold,
        )

    def delete_mailbox(self, name: str) -> None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM bus.mailboxes WHERE name = %s", (name,))
                cur.execute("DELETE FROM bus.messages WHERE mailbox = %s", (name,))
        log.info("PgBus: delete_mailbox %r", name)

    def list_mailboxes(self) -> list[str]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM bus.mailboxes ORDER BY name")
                return [row[0] for row in cur.fetchall()]

    # ── Message operations ─────────────────────────────────────────────────────

    def _mailbox_meta(self, cur, mailbox: str) -> tuple[str, int]:
        """Return (feed_type, notify_threshold) for a mailbox; defaults if not registered."""
        cur.execute(
            "SELECT feed_type, notify_threshold FROM bus.mailboxes WHERE name = %s",
            (mailbox,),
        )
        row = cur.fetchone()
        return (row[0], row[1]) if row else ("personal", 5)

    def append(self, mailbox: str, envelope: Envelope) -> None:
        channel = _channel(mailbox)
        with self._connect() as conn:
            with conn.cursor() as cur:
                feed_type, notify_threshold = self._mailbox_meta(cur, mailbox)
                if feed_type == "debug":
                    # Evict oldest message when at cap — single atomic DELETE + INSERT.
                    cur.execute(
                        "DELETE FROM bus.messages WHERE id = ("
                        "  SELECT id FROM bus.messages WHERE mailbox = %s"
                        "  ORDER BY created_at ASC LIMIT 1"
                        ") AND (SELECT count(*) FROM bus.messages WHERE mailbox = %s) >= %s",
                        (mailbox, mailbox, DEBUG_CAP),
                    )
                notified = envelope.importance >= notify_threshold
                cur.execute(
                    "INSERT INTO bus.messages"
                    " (mailbox, from_device, envelope_json, importance, notified)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (
                        mailbox, envelope.from_device, envelope.to_json(),
                        envelope.importance, notified,
                    ),
                )
                # NOTIFY always fires — devices need this to wake and fetch messages.
                # The `notified` column tells receivers whether to surface a user alert.
                cur.execute("SELECT pg_notify(%s, 'new')", (channel,))
        log.info(
            "PgBus: append mailbox=%r from=%r feed_type=%r importance=%d threshold=%d",
            mailbox, envelope.from_device, feed_type, envelope.importance, notify_threshold,
        )

    def unseen_count(self, mailbox: str) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM bus.messages WHERE mailbox = %s AND NOT seen",
                    (mailbox,),
                )
                return cur.fetchone()[0]

    def fetch_unseen(self, mailbox: str) -> list[Envelope]:
        """Fetch all unseen messages and atomically mark them seen."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE bus.messages SET seen = true
                    WHERE id IN (
                        SELECT id FROM bus.messages
                        WHERE mailbox = %s AND NOT seen
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING envelope_json
                    """,
                    (mailbox,),
                )
                rows = cur.fetchall()
        result = [_env_from_row(env_json) for (env_json,) in rows]
        if result:
            log.info("PgBus: fetch_unseen mailbox=%r count=%d", mailbox, len(result))
        return result

    def fetch_recent(self, mailbox: str, limit: int = 20) -> list[Envelope]:
        """Return the last `limit` messages without marking them seen."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT envelope_json FROM bus.messages
                    WHERE mailbox = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (mailbox, limit),
                )
                rows = cur.fetchall()
        # Return in chronological order (oldest first)
        return [_env_from_row(env_json) for (env_json,) in reversed(rows)]

    def purge_old_messages(self, retention_hours: int = 24) -> int:
        """Delete messages older than retention_hours. Returns count purged."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM bus.messages"
                    " WHERE created_at < now() - (%s || ' hours')::interval",
                    (str(retention_hours),),
                )
                count = cur.rowcount
        if count:
            log.info("PgBus: purged %d expired message(s)", count)
        return count

    def idle_wait(self, mailbox: str, timeout_s: float = 25 * 60) -> bool:
        """Block until a message arrives in mailbox or timeout_s elapses.

        Uses LISTEN/NOTIFY for push delivery. Opens a dedicated autocommit
        connection for the duration of the wait — one per listener thread,
        which is the expected usage pattern (one device process = one thread).

        Returns True if a message arrived, False if timeout expired.
        """
        # Fast path: messages already waiting
        if self.unseen_count(mailbox) > 0:
            return True

        channel = _channel(mailbox)
        conn = None
        try:
            conn = psycopg2.connect(self._dsn)
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(f"LISTEN {channel}")
            # Drain any notifications that arrived during connection setup
            conn.poll()
            if conn.notifies:
                conn.notifies.clear()
                return True
            if self.unseen_count(mailbox) > 0:
                return True

            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                remaining = deadline - time.monotonic()
                ready = select.select([conn], [], [], min(remaining, 30.0))
                if ready[0]:
                    conn.poll()
                    if conn.notifies:
                        conn.notifies.clear()
                        return True
                # Re-check in case a notify fired between select and poll
                if self.unseen_count(mailbox) > 0:
                    return True
            return False

        except Exception as exc:
            log.warning("PgBus: idle_wait LISTEN failed, falling back to poll: %s", exc)
            deadline = time.monotonic() + timeout_s
            while time.monotonic() < deadline:
                if self.unseen_count(mailbox) > 0:
                    return True
                time.sleep(min(2.0, deadline - time.monotonic()))
            return False
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass


# ── Helpers ────────────────────────────────────────────────────────────────────


def _env_from_row(env_json) -> Envelope:
    """Build Envelope from a JSONB row (dict) or raw JSON string."""
    if isinstance(env_json, dict):
        return Envelope.from_json(json.dumps(env_json))
    return Envelope.from_json(env_json)
