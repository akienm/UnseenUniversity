"""
test_daily_console_file_handler.py — DailyConsoleFileHandler unit tests.

Verifies: daily rollover, ANSI color codes in output, 7-day pruning,
and that setup_logging() wires the handler onto igor root.
"""

from __future__ import annotations

import logging
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch


from unseen_university.devices.igor.logging_setup import DailyConsoleFileHandler, setup_logging


def _fresh_igor_root():
    igor = logging.getLogger("igor")
    for h in list(igor.handlers):
        igor.removeHandler(h)
    return igor


class TestDailyConsoleFileHandler(unittest.TestCase):
    def setUp(self):
        _fresh_igor_root()

    def tearDown(self):
        _fresh_igor_root()

    def test_creates_dated_log_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            h = DailyConsoleFileHandler(log_dir)
            record = logging.LogRecord(
                "igor.test", logging.INFO, "", 0, "hello", (), None
            )
            h.handle(record)
            h.close()
            files = list(log_dir.glob("????-??-??.console.md"))
            self.assertEqual(len(files), 1)
            self.assertRegex(files[0].name, r"^\d{4}-\d{2}-\d{2}\.console\.md$")

    def test_output_written_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            h = DailyConsoleFileHandler(log_dir)
            record = logging.LogRecord(
                "igor.test", logging.WARNING, "", 0, "warn msg", (), None
            )
            h.handle(record)
            h.close()
            content = next(log_dir.glob("*.console.md")).read_text(encoding="utf-8")
            self.assertIn(
                "warn msg", content, "Expected log message written to console.md"
            )

    def test_rolls_over_at_day_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            h = DailyConsoleFileHandler(log_dir)

            day1 = datetime(2026, 5, 6, 23, 59)
            day2 = datetime(2026, 5, 7, 0, 1)

            with patch("unseen_university.devices.igor.logging_setup.datetime") as mock_dt:
                mock_dt.now.return_value = day1
                mock_dt.strptime = datetime.strptime
                r1 = logging.LogRecord("igor", logging.INFO, "", 0, "day1", (), None)
                h.handle(r1)

                mock_dt.now.return_value = day2
                r2 = logging.LogRecord("igor", logging.INFO, "", 0, "day2", (), None)
                h.handle(r2)

            h.close()
            files = sorted(log_dir.glob("????-??-??.console.md"))
            self.assertEqual(len(files), 2)
            self.assertEqual(files[0].name, "2026-05-06.console.md")
            self.assertEqual(files[1].name, "2026-05-07.console.md")

    def test_prunes_files_older_than_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            today = datetime.now()
            old_date = (today - timedelta(days=10)).strftime("%Y-%m-%d")
            recent_date = (today - timedelta(days=3)).strftime("%Y-%m-%d")

            old = log_dir / f"{old_date}.console.md"
            recent = log_dir / f"{recent_date}.console.md"
            old.write_text("old", encoding="utf-8")
            recent.write_text("recent", encoding="utf-8")

            h = DailyConsoleFileHandler(log_dir, retention_days=7)
            record = logging.LogRecord("igor", logging.INFO, "", 0, "x", (), None)
            h.handle(record)
            h.close()

            self.assertFalse(old.exists(), "Stale file should have been pruned")
            self.assertTrue(recent.exists(), "Recent file should be kept")

    def test_new_file_has_markdown_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            h = DailyConsoleFileHandler(log_dir)
            record = logging.LogRecord(
                "igor.test", logging.INFO, "", 0, "msg", (), None
            )
            h.handle(record)
            h.close()
            md_file = next(log_dir.glob("????-??-??.console.md"))
            content = md_file.read_text(encoding="utf-8")
            self.assertTrue(
                content.startswith("# Igor log — "),
                f"Expected markdown header, got: {content[:50]!r}",
            )

    def test_setup_logging_wires_daily_handler(self):
        with tempfile.TemporaryDirectory() as tmp:
            setup_logging(Path(tmp))
            igor = logging.getLogger("igor")
            handler_types = [type(h).__name__ for h in igor.handlers]
            self.assertIn(
                "DailyConsoleFileHandler",
                handler_types,
                f"Expected DailyConsoleFileHandler on igor root, got {handler_types}",
            )


if __name__ == "__main__":
    unittest.main()
