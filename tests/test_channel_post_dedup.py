"""
test_channel_post_dedup.py — T-scope-guard-echo-dedup

Verifies the in-process dedup cache suppresses repeat posts of the same
dedup_key within the window. Postgres/JSONL writes are patched so tests
stay local.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "wild_igor"))

from igor.tools import channel_post as cp


class TestDedupSuppression(unittest.TestCase):
    def setUp(self):
        # Wipe the module-level cache between tests
        with cp._DEDUP_LOCK:
            cp._DEDUP_LAST_POST.clear()

    def test_first_post_with_dedup_key_is_allowed(self):
        self.assertFalse(cp._should_suppress("scope_guard:x", 30))

    def test_second_post_same_key_suppressed_within_window(self):
        cp._should_suppress("scope_guard:x", 30)  # record first
        self.assertTrue(cp._should_suppress("scope_guard:x", 30))

    def test_different_keys_independent(self):
        cp._should_suppress("scope_guard:foo", 30)
        # Different key → not suppressed even though we just posted something
        self.assertFalse(cp._should_suppress("scope_guard:bar", 30))

    def test_zero_window_never_suppresses(self):
        cp._should_suppress("scope_guard:x", 0)
        # Window = 0 means 0*60 = 0 secs — anything > 0 elapsed is fine
        # In practice monotonic delta is always >= 0, and we check < window_secs,
        # so the second call returns False (no suppression)
        self.assertFalse(cp._should_suppress("scope_guard:x", 0))

    def test_empty_dedup_key_never_suppresses(self):
        self.assertFalse(cp._should_suppress("", 30))
        self.assertFalse(cp._should_suppress("", 30))


class TestPostToChannelIntegration(unittest.TestCase):
    def setUp(self):
        with cp._DEDUP_LOCK:
            cp._DEDUP_LAST_POST.clear()

    def test_repeat_post_skips_db_and_jsonl(self):
        """When dedup_key repeats, neither Postgres nor JSONL write fires."""
        # Patch both write targets — any call means suppression failed
        with patch("psycopg2.connect") as pg_stub, patch(
            "builtins.open", create=True
        ) as open_stub, patch.object(
            cp.paths.__wrapped__ if hasattr(cp.paths, "__wrapped__") else cp.paths,
            "__call__",
            side_effect=cp.paths,
        ):
            # First post should call pg; second should not
            cp.post_to_channel("hello", dedup_key="k1")
            first_pg_calls = pg_stub.call_count
            cp.post_to_channel("hello", dedup_key="k1")
            second_pg_calls = pg_stub.call_count
        self.assertEqual(
            second_pg_calls,
            first_pg_calls,
            "Repeat post with same dedup_key should NOT reach Postgres",
        )

    def test_no_dedup_key_always_posts(self):
        """Without dedup_key, every call tries to write (legacy behavior preserved)."""
        with patch("psycopg2.connect") as pg_stub, patch("builtins.open", create=True):
            cp.post_to_channel("hello")
            cp.post_to_channel("hello")
        self.assertEqual(pg_stub.call_count, 2)


if __name__ == "__main__":
    unittest.main()
