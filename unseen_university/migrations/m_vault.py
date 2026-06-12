#!/usr/bin/env python3
"""m_vault.py — Create vault schema for credential storage.

Usage:
    python3 unseen_university/migrations/m_vault.py

Idempotent: safe to run multiple times.

Schema:
    vault.credentials  — owner/key/value_enc rows with device scope lists
    vault.admin_sessions — 8-hour web UI admin tokens
    vault.config       — admin password bcrypt hash + other config
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import psycopg2

log = logging.getLogger(__name__)

_DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS vault;"

_CREATE_CREDENTIALS = """
CREATE TABLE IF NOT EXISTS vault.credentials (
    id               SERIAL PRIMARY KEY,
    owner            TEXT NOT NULL,
    key              TEXT NOT NULL,
    value_enc        BYTEA NOT NULL,
    allowed_devices  TEXT[] NOT NULL DEFAULT '{}',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(owner, key)
);
"""

_CREATE_ADMIN_SESSIONS = """
CREATE TABLE IF NOT EXISTS vault.admin_sessions (
    token       TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);
"""

_CREATE_CONFIG = """
CREATE TABLE IF NOT EXISTS vault.config (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS vault_creds_owner ON vault.credentials (owner);",
    "CREATE INDEX IF NOT EXISTS vault_creds_owner_key ON vault.credentials (owner, key);",
    "CREATE INDEX IF NOT EXISTS vault_sessions_expires ON vault.admin_sessions (expires_at);",
]


def migrate() -> None:
    conn = psycopg2.connect(_DB_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_SCHEMA)
            cur.execute(_CREATE_CREDENTIALS)
            cur.execute(_CREATE_ADMIN_SESSIONS)
            cur.execute(_CREATE_CONFIG)
            for idx in _CREATE_INDEXES:
                cur.execute(idx)
        log.info("vault migration complete")
    finally:
        conn.close()


def verify() -> bool:
    try:
        conn = psycopg2.connect(_DB_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            for table in ("credentials", "admin_sessions", "config"):
                cur.execute(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='vault' AND table_name=%s);",
                    (table,),
                )
                if not cur.fetchone()[0]:
                    log.error("vault.%s missing", table)
                    return False
        conn.close()
        log.info("vault migration verified")
        return True
    except Exception as exc:
        log.error("verify failed: %s", exc)
        return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    migrate()
    sys.exit(0 if verify() else 1)


if __name__ == "__main__":
    main()
