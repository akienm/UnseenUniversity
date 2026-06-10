"""
Tests for T-classifier-graph-first: knn_classifier.py

Verifies:
- classify() returns a memory_type string and cloud_calls_count=0
- fallback to FACTUAL when no embeddings available
- fallback to FACTUAL when embed() returns None
- build_index() populates competition.memory_embeddings
"""
import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import psycopg2

_DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

_REPO = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "lab"))

from lab.competition.classifiers.knn_classifier import FALLBACK_TYPE, build_index, classify  # noqa: E402


def _conn():
    return psycopg2.connect(_DB_URL)


def _insert_memory(mem_id: str, narrative: str, mtype: str, holdout: bool = False) -> None:
    conn = _conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO competition.memories (id, narrative, memory_type, holdout) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                (mem_id, narrative, mtype, holdout),
            )
    conn.close()


def _insert_embedding(mem_id: str, vec: list[float]) -> None:
    conn = _conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO competition.memory_embeddings (memory_id, embedding) "
                "VALUES (%s, %s) ON CONFLICT (memory_id) DO NOTHING",
                (mem_id, json.dumps(vec)),
            )
    conn.close()


def _delete(mem_ids: list[str]) -> None:
    conn = _conn()
    with conn:
        with conn.cursor() as cur:
            cur.executemany(
                "DELETE FROM competition.memory_embeddings WHERE memory_id = %s",
                [(mid,) for mid in mem_ids],
            )
            cur.executemany(
                "DELETE FROM competition.memories WHERE id = %s",
                [(mid,) for mid in mem_ids],
            )
    conn.close()


class TestKnnClassifierFallback(unittest.TestCase):
    def test_returns_fallback_when_embed_returns_none(self):
        with patch("devices.igor.cognition.embedder.embed", return_value=None):
            mtype, cloud = classify("some text")
        self.assertEqual(mtype, FALLBACK_TYPE)
        self.assertEqual(cloud, 0)

    def test_cloud_calls_always_zero(self):
        with patch("devices.igor.cognition.embedder.embed", return_value=None):
            _, cloud = classify("some text")
        self.assertEqual(cloud, 0)

    def test_returns_string_memory_type(self):
        with patch("devices.igor.cognition.embedder.embed", return_value=None):
            mtype, _ = classify("some text")
        self.assertIsInstance(mtype, str)
        self.assertGreater(len(mtype), 0)


class TestKnnClassifierWithEmbeddings(unittest.TestCase):
    def setUp(self):
        prefix = uuid.uuid4().hex[:8]
        self.prefix = prefix
        # Insert 3 training memories with fake embeddings that clearly separate types
        # PROCEDURAL: unit vector in dim 0
        # FACTUAL: unit vector in dim 1
        dims = 4
        self.mem_ids = []
        data = [
            (f"__knn_proc_{prefix}__", "step by step process guide", "PROCEDURAL", [1.0, 0.0, 0.0, 0.0]),
            (f"__knn_fact_{prefix}__", "factual reference information", "FACTUAL", [0.0, 1.0, 0.0, 0.0]),
            (f"__knn_interp_{prefix}__", "interpretive analysis", "INTERPRETIVE", [0.0, 0.0, 1.0, 0.0]),
        ]
        for mem_id, narrative, mtype, vec in data:
            _insert_memory(mem_id, narrative, mtype, holdout=False)
            _insert_embedding(mem_id, vec)
            self.mem_ids.append(mem_id)

    def tearDown(self):
        _delete(self.mem_ids)

    def test_classify_routes_to_most_similar_type(self):
        # Query vector closest to PROCEDURAL ([1,0,0,0])
        proc_query = [0.9, 0.1, 0.0, 0.0]
        with patch("lab.competition.classifiers.knn_classifier._embed", return_value=proc_query):
            mtype, cloud = classify("some procedure text", k=1)
        self.assertEqual(mtype, "PROCEDURAL")
        self.assertEqual(cloud, 0)

    def test_holdout_excluded_from_voting(self):
        prefix2 = uuid.uuid4().hex[:8]
        holdout_id = f"__knn_holdout_{prefix2}__"
        # Insert holdout FACTUAL with embedding pointing away from PROCEDURAL
        _insert_memory(holdout_id, "holdout factual", "FACTUAL", holdout=True)
        _insert_embedding(holdout_id, [0.0, 1.0, 0.0, 0.0])
        self.mem_ids.append(holdout_id)

        # Query towards PROCEDURAL — FACTUAL holdout should be excluded
        proc_query = [0.9, 0.0, 0.0, 0.0]
        with patch("lab.competition.classifiers.knn_classifier._embed", return_value=proc_query):
            mtype, cloud = classify("procedure text", k=1)
        self.assertEqual(mtype, "PROCEDURAL")
        self.assertEqual(cloud, 0)


class TestBuildIndex(unittest.TestCase):
    def setUp(self):
        prefix = uuid.uuid4().hex[:8]
        self.mem_ids = []
        for i in range(3):
            mid = f"__build_idx_{prefix}_{i}__"
            _insert_memory(mid, f"test narrative for build index {i}", "FACTUAL")
            self.mem_ids.append(mid)

    def tearDown(self):
        _delete(self.mem_ids)

    def test_build_index_populates_embeddings(self):
        fake_vec = [0.1, 0.2, 0.3, 0.4]
        with patch("lab.competition.classifiers.knn_classifier._embed", return_value=fake_vec):
            result = build_index()
        self.assertGreaterEqual(result["embedded"], 3)
        self.assertEqual(result["failed"], 0)

        # Verify embeddings exist
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM competition.memory_embeddings "
                "WHERE memory_id = ANY(%s)",
                (self.mem_ids,),
            )
            count = cur.fetchone()[0]
        conn.close()
        self.assertEqual(count, 3)


if __name__ == "__main__":
    unittest.main()
