"""
Tests for T-classifier-prompt-first: llm_classifier.py

Verifies:
- classify() returns a memory_type string and cloud_calls_count
- Cache hit returns cloud_calls_count=0 without LLM call
- Cache miss calls LLM and returns cloud_calls_count=1
- Returns FALLBACK when LLM unavailable
- Second call for same text returns cloud_calls_count=0
"""
import hashlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import psycopg2

_DB_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-Wild1",
)

_REPO = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "lab"))

from devlab.competition.classifiers.llm_classifier import (  # noqa: E402
    FALLBACK_TYPE,
    _cache_get,
    _cache_put,
    _ensure_cache_table,
    _sha256,
    classify,
)


def _conn():
    return psycopg2.connect(_DB_URL)


def _delete_cache(text_hash: str) -> None:
    conn = _conn()
    with conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM competition.classifications_cache WHERE text_hash = %s",
                (text_hash,),
            )
    conn.close()


class TestLlmClassifierCache(unittest.TestCase):
    def setUp(self):
        _ensure_cache_table()
        self.test_text = "unique test text for llm classifier 8f2a9b3c"
        self.text_hash = _sha256(self.test_text)
        _delete_cache(self.text_hash)

    def tearDown(self):
        _delete_cache(self.text_hash)

    def test_returns_string_memory_type(self):
        with patch("devlab.competition.classifiers.llm_classifier._call_llm", return_value="FACTUAL"):
            mtype, cloud = classify(self.test_text)
        self.assertIsInstance(mtype, str)
        self.assertGreater(len(mtype), 0)

    def test_returns_one_cloud_call_on_cache_miss(self):
        with patch("devlab.competition.classifiers.llm_classifier._call_llm", return_value="PROCEDURAL"):
            mtype, cloud = classify(self.test_text)
        self.assertEqual(cloud, 1)
        self.assertEqual(mtype, "PROCEDURAL")

    def test_second_call_returns_zero_cloud_calls(self):
        with patch("devlab.competition.classifiers.llm_classifier._call_llm", return_value="EPISODIC") as mock_llm:
            classify(self.test_text)  # first call — writes cache
            mtype2, cloud2 = classify(self.test_text)  # second call — cache hit
        self.assertEqual(cloud2, 0)
        self.assertEqual(mtype2, "EPISODIC")
        # LLM called exactly once despite two classify() calls
        self.assertEqual(mock_llm.call_count, 1)

    def test_fallback_when_llm_returns_none(self):
        with patch("devlab.competition.classifiers.llm_classifier._call_llm", return_value=None):
            mtype, cloud = classify(self.test_text)
        self.assertEqual(mtype, FALLBACK_TYPE)
        self.assertEqual(cloud, 1)

    def test_fallback_result_not_cached(self):
        """Fallback returns are not cached so a later LLM recovery works."""
        with patch("devlab.competition.classifiers.llm_classifier._call_llm", return_value=None):
            classify(self.test_text)
        # Now LLM is "back" — should make another call, not return cached fallback
        with patch("devlab.competition.classifiers.llm_classifier._call_llm", return_value="CONCEPTUAL") as mock_llm:
            mtype2, cloud2 = classify(self.test_text)
        self.assertEqual(mtype2, "CONCEPTUAL")
        self.assertEqual(cloud2, 1)
        self.assertEqual(mock_llm.call_count, 1)


class TestCacheDirectly(unittest.TestCase):
    def setUp(self):
        _ensure_cache_table()
        self.text_hash = "test_" + "a" * 60  # 64-char fake hash

    def tearDown(self):
        _delete_cache(self.text_hash)

    def test_cache_roundtrip(self):
        self.assertIsNone(_cache_get(self.text_hash))
        _cache_put(self.text_hash, "INTERPRETIVE", "test-model")
        result = _cache_get(self.text_hash)
        self.assertEqual(result, "INTERPRETIVE")

    def test_sha256_deterministic(self):
        h1 = _sha256("hello world")
        h2 = _sha256("hello world")
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)


if __name__ == "__main__":
    unittest.main()
