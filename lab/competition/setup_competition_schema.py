#!/usr/bin/env python3
"""
Create competition schema for inference classifier competition.

Mirrors clan.memories + clan.memory_embeddings in an isolated competition
schema so classifiers train/eval on clean programming-book data without
touching production memories.

Usage:
    python lab/competition/setup_competition_schema.py
    python lab/competition/setup_competition_schema.py --drop-recreate
"""
import argparse
import os
import sys

import psycopg2


_DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

_CREATE_SCHEMA = "CREATE SCHEMA IF NOT EXISTS competition;"

_CREATE_MEMORIES = """
CREATE TABLE IF NOT EXISTS competition.memories (
    id                  text NOT NULL PRIMARY KEY,
    narrative           text,
    memory_type         text,
    parent_id           text,
    children_ids        text DEFAULT '[]'::text,
    link_ids            text DEFAULT '[]'::text,
    valence             real DEFAULT 0.0,
    activation_count    integer DEFAULT 0,
    friction_history    text DEFAULT '[]'::text,
    timestamp           text,
    metadata            jsonb DEFAULT '{}'::jsonb,
    embedding           text,
    arousal             real DEFAULT 0.0,
    dominance           real DEFAULT 0.0,
    portable            integer DEFAULT 1,
    links_weighted      text DEFAULT '{}'::text,
    last_accessed       text,
    source              text,
    confidence          real DEFAULT 1.0,
    context_of_encoding text,
    updated_at          text,
    scope               text DEFAULT 'class'::text,
    payload             text,
    activation_score    double precision DEFAULT 0.0,
    last_activated_at   timestamp with time zone,
    payloads            jsonb,
    source_agent        character varying(128) DEFAULT NULL,
    source_token        character varying(256) DEFAULT NULL,
    derived_from        text[],
    tags                jsonb DEFAULT '[]'::jsonb,
    triggers            jsonb DEFAULT '{}'::jsonb,
    holdout             boolean DEFAULT false
);
"""

_CREATE_MEMORY_EMBEDDINGS = """
CREATE TABLE IF NOT EXISTS competition.memory_embeddings (
    memory_id   text NOT NULL PRIMARY KEY,
    embedding   text,
    FOREIGN KEY (memory_id) REFERENCES competition.memories(id) ON DELETE CASCADE
);
"""

_CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_comp_mem_type ON competition.memories(memory_type);",
    "CREATE INDEX IF NOT EXISTS idx_comp_mem_holdout ON competition.memories(holdout);",
    "CREATE INDEX IF NOT EXISTS idx_comp_mem_type_holdout ON competition.memories(memory_type, holdout);",
    "CREATE INDEX IF NOT EXISTS idx_comp_mem_source ON competition.memories(source);",
]

_DROP_SCHEMA = "DROP SCHEMA IF EXISTS competition CASCADE;"


def setup(drop_recreate: bool = False) -> None:
    conn = psycopg2.connect(_DB_URL)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            if drop_recreate:
                print("Dropping competition schema (--drop-recreate)...")
                cur.execute(_DROP_SCHEMA)

            print("Creating competition schema...")
            cur.execute(_CREATE_SCHEMA)

            print("Creating competition.memories...")
            cur.execute(_CREATE_MEMORIES)

            print("Creating competition.memory_embeddings...")
            cur.execute(_CREATE_MEMORY_EMBEDDINGS)

            print("Creating indexes...")
            for stmt in _CREATE_INDEXES:
                cur.execute(stmt)

            conn.commit()
            print("Schema committed.")

            # Verify
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'competition' ORDER BY table_name"
            )
            tables = [r[0] for r in cur.fetchall()]
            print(f"\nVerification — competition.* tables: {tables}")
            assert "memories" in tables, "competition.memories missing"
            assert "memory_embeddings" in tables, "competition.memory_embeddings missing"

            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'competition' AND table_name = 'memories' "
                "ORDER BY ordinal_position"
            )
            cols = [r[0] for r in cur.fetchall()]
            print(f"competition.memories columns ({len(cols)}): {', '.join(cols)}")
            assert "holdout" in cols, "holdout column missing from competition.memories"

        print("\nOK — competition schema ready.")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--drop-recreate",
        action="store_true",
        help="Drop and recreate the competition schema (destroys all data)",
    )
    args = parser.parse_args()
    setup(drop_recreate=args.drop_recreate)


if __name__ == "__main__":
    main()
