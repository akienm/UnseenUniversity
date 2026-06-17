"""
Regression test for T-test-inbox-tagging — scope-tagged cc_inbox writes
plus delete_by_prefix sweep.

Tests pollute the production cc_inbox; the tag-and-sweep mechanism stamps
every write with `[test:<ts>]: ` prefix and removes the matching entries
on session teardown. Generalizes to any scope (debug:, sandbox:, dev:).
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from devlab.claudecode.cc_inbox import (
    _load_all,
    append,
    delete_by_prefix,
)


class TestCcInboxTagging(unittest.TestCase):
    def setUp(self):
        self._saved_tag = os.environ.pop("CC_INBOX_TAG", None)
        self._tmp = TemporaryDirectory()
        self.inbox_path = Path(self._tmp.name) / "inbox.jsonl"

    def tearDown(self):
        if self._saved_tag is not None:
            os.environ["CC_INBOX_TAG"] = self._saved_tag
        else:
            os.environ.pop("CC_INBOX_TAG", None)
        self._tmp.cleanup()

    def test_append_without_tag_leaves_summary_unprefixed(self):
        e = append(
            kind="test_kind",
            summary="bare summary",
            path=self.inbox_path,
        )
        self.assertEqual(e.summary, "bare summary")
        loaded = _load_all(self.inbox_path)
        self.assertEqual(loaded[0].summary, "bare summary")

    def test_append_with_tag_prepends_bracketed_prefix(self):
        os.environ["CC_INBOX_TAG"] = "test:20260501.180000.123456"
        e = append(
            kind="test_kind",
            summary="tagged summary",
            path=self.inbox_path,
        )
        self.assertEqual(e.summary, "[test:20260501.180000.123456]: tagged summary")
        loaded = _load_all(self.inbox_path)
        self.assertTrue(loaded[0].summary.startswith("[test:20260501.180000.123456]: "))

    def test_empty_tag_treated_as_unset(self):
        os.environ["CC_INBOX_TAG"] = "   "
        e = append(kind="k", summary="no tag", path=self.inbox_path)
        self.assertEqual(e.summary, "no tag")

    def test_delete_by_prefix_removes_only_matching(self):
        # Mix tagged + untagged + other-tagged entries.
        os.environ["CC_INBOX_TAG"] = "test:run1"
        append(kind="k", summary="this gets swept", path=self.inbox_path)
        append(kind="k", summary="also swept", path=self.inbox_path)
        os.environ["CC_INBOX_TAG"] = "test:run2"
        append(kind="k", summary="other run kept", path=self.inbox_path)
        os.environ.pop("CC_INBOX_TAG")
        append(kind="k", summary="untagged production entry", path=self.inbox_path)

        removed = delete_by_prefix("[test:run1]", path=self.inbox_path)
        self.assertEqual(removed, 2)

        remaining = _load_all(self.inbox_path)
        summaries = sorted(e.summary for e in remaining)
        self.assertEqual(
            summaries,
            ["[test:run2]: other run kept", "untagged production entry"],
        )

    def test_delete_by_prefix_broad_match_on_test_root(self):
        """`delete_by_prefix("[test:")` sweeps ALL test runs (manual fallback)."""
        os.environ["CC_INBOX_TAG"] = "test:run1"
        append(kind="k", summary="run1", path=self.inbox_path)
        os.environ["CC_INBOX_TAG"] = "test:run2"
        append(kind="k", summary="run2", path=self.inbox_path)
        os.environ.pop("CC_INBOX_TAG")
        append(kind="k", summary="prod", path=self.inbox_path)

        removed = delete_by_prefix("[test:", path=self.inbox_path)
        self.assertEqual(removed, 2)
        remaining = _load_all(self.inbox_path)
        self.assertEqual([e.summary for e in remaining], ["prod"])

    def test_delete_by_prefix_no_match_returns_zero(self):
        append(kind="k", summary="untagged", path=self.inbox_path)
        removed = delete_by_prefix("[test:nomatch]", path=self.inbox_path)
        self.assertEqual(removed, 0)
        self.assertEqual(len(_load_all(self.inbox_path)), 1)

    def test_delete_by_prefix_handles_missing_inbox(self):
        missing = Path(self._tmp.name) / "does_not_exist.jsonl"
        removed = delete_by_prefix("[test:anything]", path=missing)
        self.assertEqual(removed, 0)

    def test_delete_by_prefix_empty_prefix_is_noop(self):
        append(kind="k", summary="x", path=self.inbox_path)
        removed = delete_by_prefix("", path=self.inbox_path)
        self.assertEqual(removed, 0)
        self.assertEqual(len(_load_all(self.inbox_path)), 1)

    def test_tag_and_sweep_round_trip(self):
        """End-to-end: tag a write, sweep with the matching prefix, verify gone."""
        os.environ["CC_INBOX_TAG"] = "test:e2e.20260501"
        append(kind="trip", summary="should disappear", path=self.inbox_path)
        # Confirm it landed with the prefix.
        before = _load_all(self.inbox_path)
        self.assertEqual(len(before), 1)
        self.assertTrue(before[0].summary.startswith("[test:e2e.20260501]: "))
        # Sweep.
        removed = delete_by_prefix("[test:e2e.20260501]", path=self.inbox_path)
        self.assertEqual(removed, 1)
        self.assertEqual(_load_all(self.inbox_path), [])


if __name__ == "__main__":
    unittest.main()
