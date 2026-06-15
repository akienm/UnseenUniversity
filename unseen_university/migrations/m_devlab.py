#!/usr/bin/env python3
"""m_devlab.py — Create devlab schema and initial tables.

Usage:
    python3 unseen_university/migrations/m_devlab.py

Idempotent: safe to run multiple times.
  - Creates schema devlab if not exists
  - Creates table devlab.constraints if not exists
  - Creates table devlab.tickets (stub) if not exists
  - Creates indexes if not exists

Schema:
    devlab.constraints
        id SERIAL PRIMARY KEY
        text TEXT NOT NULL
        kind TEXT NOT NULL          -- 'rule' | 'safeguard' | 'pattern' | 'design'
        severity TEXT               -- 'high' | 'medium' | 'low'
        applies_to JSONB            -- {"files": [...], "tags": [...], "domains": [...]}
        source JSONB                -- {"path": "palace/...", "section": "..."}
        implies JSONB[]             -- related constraint references
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

    devlab.tickets (stub for cc_queue writer migration)
        id TEXT PRIMARY KEY         -- 'T-xxx'
        status TEXT NOT NULL
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
    "UU_HOME_DB_URL",
    os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    ),
)

# ── SQL ───────────────────────────────────────────────────────────────────────

_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS devlab;"

_CREATE_CONSTRAINTS = """
CREATE TABLE IF NOT EXISTS devlab.constraints (
    id SERIAL PRIMARY KEY,
    text TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT,
    applies_to JSONB,
    source JSONB,
    implies JSONB[],
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_CREATE_TICKETS_STUB = """
CREATE TABLE IF NOT EXISTS devlab.tickets (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS devlab_constraints_kind ON devlab.constraints (kind);",
    "CREATE INDEX IF NOT EXISTS devlab_constraints_severity ON devlab.constraints (severity);",
    "CREATE INDEX IF NOT EXISTS devlab_constraints_applies_to ON devlab.constraints USING GIN (applies_to);",
    "CREATE INDEX IF NOT EXISTS devlab_constraints_created_at ON devlab.constraints (created_at DESC);",
    "CREATE INDEX IF NOT EXISTS devlab_tickets_status ON devlab.tickets (status);",
]


def migrate() -> None:
    """Execute migration: create schema, tables, and indexes."""
    conn = psycopg2.connect(_DB_URL)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_SCHEMA)
            cur.execute(_CREATE_CONSTRAINTS)
            cur.execute(_CREATE_TICKETS_STUB)
            for idx_sql in _CREATE_INDEXES:
                cur.execute(idx_sql)
        log.info("devlab migration completed")
    except psycopg2.Error as e:
        log.error("Database error: %s", e)
        raise
    finally:
        conn.close()


def verify() -> bool:
    """Verify devlab.constraints and devlab.tickets exist."""
    try:
        conn = psycopg2.connect(_DB_URL)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'devlab' ORDER BY table_name;"
            )
            tables = {row[0] for row in cur.fetchall()}
        conn.close()
        required = {"constraints", "tickets"}
        missing = required - tables
        if missing:
            log.error("Missing devlab tables: %s", missing)
            return False
        log.info("devlab schema verified: %s", sorted(tables))
        return True
    except Exception as e:
        log.error("Verification failed: %s", e)
        return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    migrate()
    if verify():
        log.info("✓ devlab migration verified")
        sys.exit(0)
    else:
        log.error("✗ devlab migration verification failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
