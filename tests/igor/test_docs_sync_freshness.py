"""
test_docs_sync_freshness.py — Tests for T-IGOR-DOCNODES-006 freshness signal.

Covers:
  - _parse_dsb: content_hash field present and consistent
  - _upsert_entries: last_modified preserved when content unchanged
  - _upsert_entries: last_modified updated when content changes
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "claudecode"))


class TestParseDsbContentHash(unittest.TestCase):
    def setUp(self):
        from docs_sync import _parse_dsb

        self._parse_dsb = _parse_dsb

    def _write_dsb(self, lines: list[str]) -> Path:
        tmp = tempfile.NamedTemporaryFile(
            suffix=".dsb", mode="w", delete=False, encoding="utf-8"
        )
        tmp.write("\n".join(lines))
        tmp.flush()
        return Path(tmp.name)

    def test_content_hash_present(self):
        p = self._write_dsb(["D001|short-name|decided|Some decision here"])
        entries = self._parse_dsb(p)
        self.assertTrue(len(entries) > 0)
        self.assertIn("content_hash", entries[0])
        self.assertIsNotNone(entries[0]["content_hash"])
        self.assertEqual(len(entries[0]["content_hash"]), 32)  # MD5 hex

    def test_content_hash_deterministic(self):
        """Same line → same hash across two parse calls."""
        p = self._write_dsb(["D001|short-name|decided|Same content"])
        e1 = self._parse_dsb(p)
        e2 = self._parse_dsb(p)
        self.assertEqual(e1[0]["content_hash"], e2[0]["content_hash"])

    def test_different_content_different_hash(self):
        p1 = self._write_dsb(["D001|short-name|decided|Content A"])
        p2 = self._write_dsb(["D001|short-name|decided|Content B"])
        e1 = self._parse_dsb(p1)
        e2 = self._parse_dsb(p2)
        self.assertNotEqual(e1[0]["content_hash"], e2[0]["content_hash"])


class TestUpsertFreshness(unittest.TestCase):
    """
    Verify that last_modified is only bumped when content actually changes.
    We test the SQL logic indirectly via _upsert_entries with a live DB connection
    mock that captures the SQL sent.
    """

    def _make_entry(self, content: str) -> dict:
        import hashlib

        return {
            "source": "test_source",
            "entry_key": "T001",
            "entry_type": "test",
            "content": content,
            "content_hash": hashlib.md5(content.encode()).hexdigest(),
            "synced_at": "2026-04-03T00:00",
        }

    def test_upsert_sql_includes_content_hash_column(self):
        """The INSERT statement must name content_hash."""
        from docs_sync import _upsert_entries

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("docs_sync._conn", return_value=mock_conn):
            _upsert_entries([self._make_entry("hello")])

        calls = mock_cur.execute.call_args_list
        self.assertTrue(len(calls) > 0)
        sql = calls[0][0][0]
        self.assertIn("content_hash", sql)
        self.assertIn("last_modified", sql)

    def test_upsert_sql_uses_case_for_last_modified(self):
        """SQL must contain a CASE expression to guard last_modified update."""
        from docs_sync import _upsert_entries

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch("docs_sync._conn", return_value=mock_conn):
            _upsert_entries([self._make_entry("hello")])

        sql = mock_cur.execute.call_args_list[0][0][0]
        self.assertIn("CASE", sql)
        self.assertIn("IS DISTINCT FROM", sql)

    def test_empty_entries_returns_zero(self):
        from docs_sync import _upsert_entries

        result = _upsert_entries([])
        self.assertEqual(result, 0)


if __name__ == "__main__":
    unittest.main()
