"""
Tests for T-competition-schema: setup_competition_schema.py

Verifies that the competition schema tables exist and accept inserts
matching the clan schema shape.
"""
import os
import unittest

import psycopg2


_DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)


def _connect():
    return psycopg2.connect(_DB_URL)


class TestCompetitionSchemaExists(unittest.TestCase):
    def test_tables_exist(self):
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'competition' ORDER BY table_name"
            )
            tables = {r[0] for r in cur.fetchall()}
        conn.close()
        self.assertIn("memories", tables)
        self.assertIn("memory_embeddings", tables)

    def test_memories_has_holdout_column(self):
        conn = _connect()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'competition' AND table_name = 'memories'"
            )
            cols = {r[0] for r in cur.fetchall()}
        conn.close()
        self.assertIn("holdout", cols)
        self.assertIn("memory_type", cols)
        self.assertIn("narrative", cols)

    def test_memories_accepts_insert(self):
        conn = _connect()
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO competition.memories (id, narrative, memory_type, holdout) "
                    "VALUES (%s, %s, %s, %s)",
                    ("__test_schema_check__", "test narrative", "FACTUAL", False),
                )
                cur.execute(
                    "SELECT id, memory_type, holdout FROM competition.memories WHERE id = %s",
                    ("__test_schema_check__",),
                )
                row = cur.fetchone()
            self.assertEqual(row[0], "__test_schema_check__")
            self.assertEqual(row[1], "FACTUAL")
            self.assertFalse(row[2])
        finally:
            conn.rollback()
            conn.close()

    def test_memory_embeddings_fk_enforced(self):
        """Inserting an embedding for a non-existent memory raises IntegrityError."""
        conn = _connect()
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                with self.assertRaises(psycopg2.IntegrityError):
                    cur.execute(
                        "INSERT INTO competition.memory_embeddings (memory_id, embedding) "
                        "VALUES (%s, %s)",
                        ("__nonexistent__", "[0.1, 0.2, 0.3]"),
                    )
        finally:
            conn.rollback()
            conn.close()


if __name__ == "__main__":
    unittest.main()
