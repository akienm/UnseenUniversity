#!/usr/bin/env python3
"""m_hubert_audit_flags.py — Create hubert.audit_flags table for structural audit results.

Usage:
    python3 unseen_university/migrations/m_hubert_audit_flags.py

Idempotent: safe to run multiple times.
  - Creates schema hubert if not exists
  - Creates table audit_flags if not exists
  - Creates indexes if not exists
  - Re-runs produce no errors

Schema:
    id SERIAL PRIMARY KEY
    ticket_id TEXT
    commit_hash TEXT
    size TEXT
    structural_score REAL
    diff_lines INT
    flag_reason TEXT
    reviewed_at TIMESTAMPTZ
    verdict TEXT
    created_at TIMESTAMPTZ DEFAULT now()
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import psycopg2

log = logging.getLogger(__name__)

_DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

# ── SQL ───────────────────────────────────────────────────────────────────────

_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS hubert;"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS hubert.audit_flags (
    id SERIAL PRIMARY KEY,
    ticket_id TEXT,
    commit_hash TEXT,
    size TEXT,
    structural_score REAL,
    diff_lines INT,
    flag_reason TEXT,
    reviewed_at TIMESTAMPTZ,
    verdict TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS audit_flags_ticket_id ON hubert.audit_flags (ticket_id);",
    "CREATE INDEX IF NOT EXISTS audit_flags_commit_hash ON hubert.audit_flags (commit_hash);",
    "CREATE INDEX IF NOT EXISTS audit_flags_created_at ON hubert.audit_flags (created_at DESC);",
    "CREATE INDEX IF NOT EXISTS audit_flags_verdict ON hubert.audit_flags (verdict);",
]


def _ts() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def migrate() -> None:
    """Execute migration: create schema, table, and indexes."""
    try:
        conn = psycopg2.connect(_DB_URL)
        conn.autocommit = True
        log.info("Connected to database: %s", _DB_URL)

        with conn.cursor() as cur:
            # Create schema
            log.info("Creating schema hubert...")
            cur.execute(_CREATE_SCHEMA)
            log.info("  Schema created or already exists")

            # Create table
            log.info("Creating table hubert.audit_flags...")
            cur.execute(_CREATE_TABLE)
            log.info("  Table created or already exists")

            # Create indexes
            for idx_sql in _CREATE_INDEXES:
                log.info("Creating index: %s", idx_sql.split("ON")[1].split("(")[0].strip())
                cur.execute(idx_sql)
            log.info("  All indexes created or already exist")

        conn.close()
        log.info("Migration completed successfully")

    except psycopg2.Error as e:
        log.error("Database error during migration: %s", e)
        raise
    except Exception as e:
        log.error("Unexpected error during migration: %s", e)
        raise


def verify() -> bool:
    """Verify that the table exists and is queryable."""
    try:
        conn = psycopg2.connect(_DB_URL)
        conn.autocommit = True

        with conn.cursor() as cur:
            # Check if table exists
            cur.execute(
                "SELECT EXISTS(SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='hubert' AND table_name='audit_flags');"
            )
            exists = cur.fetchone()[0]
            if not exists:
                log.error("Table hubert.audit_flags does not exist")
                return False

            # Check table structure
            cur.execute(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema='hubert' AND table_name='audit_flags' "
                "ORDER BY ordinal_position;"
            )
            columns = cur.fetchall()
            log.info("Table structure verified: %d columns", len(columns))
            for col_name, col_type in columns:
                log.info("  - %s: %s", col_name, col_type)

        conn.close()
        return True

    except Exception as e:
        log.error("Verification failed: %s", e)
        return False


def main() -> None:
    """Main entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    log.info("Starting migration: m_hubert_audit_flags")
    migrate()
    log.info("Verifying migration...")
    if verify():
        log.info("✓ Migration verified successfully")
        sys.exit(0)
    else:
        log.error("✗ Migration verification failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
