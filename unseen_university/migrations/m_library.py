#!/usr/bin/env python3
"""m_library.py — Create library schema and initial tables.

Usage:
    python3 unseen_university/migrations/m_library.py

Idempotent: safe to run multiple times.
  - Creates schema library if not exists
  - Creates table library.knowledge if not exists
  - Creates indexes if not exists

Schema:
    library.knowledge
        id SERIAL PRIMARY KEY
        title TEXT NOT NULL
        content TEXT NOT NULL
        domain TEXT NOT NULL          -- e.g. 'coding', 'architecture', 'people'
        tags JSONB                    -- ["tag1", "tag2", ...]
        source_ref TEXT               -- e.g. 'palace.decisions.D-xxx' or 'T-xxx'
        curated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()

Sole writer: Librarian device. Agents query via MCP tool.
"""

from __future__ import annotations
from unseen_university.identity import home_db_url

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import psycopg2

log = logging.getLogger(__name__)

# ── SQL ───────────────────────────────────────────────────────────────────────

_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS library;"

_CREATE_KNOWLEDGE = """
CREATE TABLE IF NOT EXISTS library.knowledge (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    domain TEXT NOT NULL,
    tags JSONB,
    source_ref TEXT,
    curated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS library_knowledge_domain ON library.knowledge (domain);",
    "CREATE INDEX IF NOT EXISTS library_knowledge_tags ON library.knowledge USING GIN (tags);",
    "CREATE INDEX IF NOT EXISTS library_knowledge_curated_at ON library.knowledge (curated_at DESC);",
]


def migrate() -> None:
    """Execute migration: create schema, tables, and indexes."""
    conn = psycopg2.connect(home_db_url())
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_SCHEMA)
            cur.execute(_CREATE_KNOWLEDGE)
            for idx_sql in _CREATE_INDEXES:
                cur.execute(idx_sql)
        log.info("library migration completed")
    except psycopg2.Error as e:
        log.error("Database error: %s", e)
        raise
    finally:
        conn.close()


def verify() -> bool:
    """Verify library.knowledge exists."""
    try:
        conn = psycopg2.connect(home_db_url())
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'library' ORDER BY table_name;"
            )
            tables = {row[0] for row in cur.fetchall()}
        conn.close()
        required = {"knowledge"}
        missing = required - tables
        if missing:
            log.error("Missing library tables: %s", missing)
            return False
        log.info("library schema verified: %s", sorted(tables))
        return True
    except Exception as e:
        log.error("Verification failed: %s", e)
        return False


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    migrate()
    if verify():
        log.info("✓ library migration verified")
        sys.exit(0)
    else:
        log.error("✗ library migration verification failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
