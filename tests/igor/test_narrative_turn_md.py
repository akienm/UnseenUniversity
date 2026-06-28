"""
test_narrative_turn_md.py — T-igor-logs-to-markdown

Verifies that synthesize_turn_trace() writes to narrative_turn.YYYYMMDD.md
(not .log), includes a markdown header on new files, and prunes old .md files.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from unseen_university.devices.igor.cognition import forensic_logger


def _minimal_ctx() -> dict:
    return {
        "turn_id": "test-turn",
        "thread_id": "test:thread",
        "ts": "2026-06-01T00:00:00",
        "input": "hello",
        "response": {"tier": "tier.2", "preview": "hi", "total_ms": 100},
    }


class TestNarrativeTurnMd(unittest.TestCase):
    def _call(self, log_dir: Path, ctx: dict | None = None) -> None:
        with patch.object(forensic_logger, "LOG_DIR", log_dir):
            forensic_logger.synthesize_turn_trace(ctx or _minimal_ctx())

    def test_creates_md_file_not_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            self._call(log_dir)
            md_files = list(log_dir.glob("narrative_turn.*.md"))
            log_files = list(log_dir.glob("narrative_turn.*.log"))
            self.assertEqual(len(md_files), 1, "expected exactly one .md file")
            self.assertEqual(len(log_files), 0, "no .log file should be created")

    def test_new_file_has_markdown_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            self._call(log_dir)
            content = next(log_dir.glob("narrative_turn.*.md")).read_text("utf-8")
            self.assertTrue(
                content.startswith("# Igor narrative — "),
                f"Expected markdown header, got: {content[:60]!r}",
            )

    def test_header_not_duplicated_on_second_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            self._call(log_dir)
            self._call(log_dir)
            content = next(log_dir.glob("narrative_turn.*.md")).read_text("utf-8")
            self.assertEqual(
                content.count("# Igor narrative — "),
                1,
                "header should appear exactly once per file",
            )

    def test_prune_removes_old_md_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            old_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
            recent_date = (datetime.now() - timedelta(days=3)).strftime("%Y%m%d")

            old_md = log_dir / f"narrative_turn.{old_date}.md"
            recent_md = log_dir / f"narrative_turn.{recent_date}.md"
            old_md.write_text("old", encoding="utf-8")
            recent_md.write_text("recent", encoding="utf-8")

            self._call(log_dir)

            self.assertFalse(old_md.exists(), "old .md file should be pruned")
            self.assertTrue(recent_md.exists(), "recent .md file should be kept")

    def test_prune_leaves_old_log_files_alone(self):
        """Old .log files are not touched by the new purge logic (they just age out)."""
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            old_date = (datetime.now() - timedelta(days=10)).strftime("%Y%m%d")
            old_log = log_dir / f"narrative_turn.{old_date}.log"
            old_log.write_text("legacy", encoding="utf-8")

            self._call(log_dir)

            self.assertTrue(
                old_log.exists(),
                "legacy .log files are not touched by the .md purge loop",
            )


if __name__ == "__main__":
    unittest.main()
