"""
Tests for T-book-learner-completion: per-book READING_<hash>.md report file.
Verifies header creation, chunk line appending, footer writing, and file naming.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestBookLearnerReport(unittest.TestCase):
    """READING_<hash>.md report file written by book_learner."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._progress_dir = Path(self._tmpdir.name)
        # Patch PROGRESS_DIR so reports land in temp dir
        import claudecode.book_learner as bl

        self._bl = bl
        self._orig_dir = bl.PROGRESS_DIR
        bl.PROGRESS_DIR = self._progress_dir

    def tearDown(self):
        self._bl.PROGRESS_DIR = self._orig_dir
        self._tmpdir.cleanup()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _book_key(self):
        return "Design Patterns|1234"

    def _report_file(self):
        return self._bl._report_path(self._book_key())

    # ── tests ─────────────────────────────────────────────────────────────────

    def test_report_file_named_after_reading_hash(self):
        """Report filename matches READING_<hash> — same ID as Postgres node."""
        rid = self._bl._report_id(self._book_key())
        self.assertTrue(rid.startswith("READING_"))
        self.assertEqual(len(rid), len("READING_") + 8)  # 8 hex chars, uppercased

        self._bl._write_report_header(
            book_key=self._book_key(),
            book_title="Design Patterns",
            author="Gang of Four",
            model="cloud/sonnet",
            calibre_id=1234,
        )
        self.assertTrue(self._report_file().exists())
        self.assertEqual(self._report_file().stem, rid)

    def test_report_header_created_at_start(self):
        """Header is written with title, author, model, date, and READING_ID."""
        self._bl._write_report_header(
            book_key=self._book_key(),
            book_title="Design Patterns",
            author="Gang of Four",
            model="cloud/sonnet",
            calibre_id=1234,
        )
        content = self._report_file().read_text()
        self.assertIn("Design Patterns", content)
        self.assertIn("Gang of Four", content)
        self.assertIn("cloud/sonnet", content)
        self.assertIn(self._bl._report_id(self._book_key()), content)
        # Header should start with a markdown heading
        self.assertTrue(content.startswith("#"))

    def test_chunk_lines_appended(self):
        """Each chunk call appends exactly one line to the report."""
        self._bl._write_report_header(
            book_key=self._book_key(),
            book_title="Design Patterns",
            author="GoF",
            model="local",
            calibre_id=1234,
        )
        self._bl._append_report_chunk(
            book_key=self._book_key(),
            chunk_label="[001]",
            n_deposited=3,
            summary="Creational patterns overview",
            is_error=False,
            model_tag="cloud",
        )
        self._bl._append_report_chunk(
            book_key=self._book_key(),
            chunk_label="[002]",
            n_deposited=0,
            summary="TIMEOUT: local model unavailable",
            is_error=True,
            model_tag="local",
        )
        content = self._report_file().read_text()
        # Two chunk lines appended
        lines = [l for l in content.splitlines() if l.startswith("-")]
        self.assertEqual(len(lines), 2)
        # First line: success — shows node count + summary
        self.assertIn("[001]", lines[0])
        self.assertIn("3", lines[0])
        self.assertIn("cloud", lines[0])
        # Second line: error — shows ERROR marker
        self.assertIn("[002]", lines[1])
        self.assertIn("✗", lines[1])

    def test_footer_written_with_totals(self):
        """Footer section includes chunk count, deposited count, and status."""
        self._bl._write_report_header(
            book_key=self._book_key(),
            book_title="Design Patterns",
            author="GoF",
            model="cloud",
            calibre_id=1234,
        )
        self._bl._write_report_footer(
            book_key=self._book_key(),
            chunks_done=42,
            total_deposited=157,
            errors=3,
            status="complete",
        )
        content = self._report_file().read_text()
        self.assertIn("42", content)
        self.assertIn("157", content)
        self.assertIn("complete", content)
        # Footer should appear after header (file has more than one section)
        self.assertIn("##", content)

    def test_header_overwrites_on_rerun(self):
        """Writing the header twice replaces the file (re-run starts fresh)."""
        self._bl._write_report_header(
            book_key=self._book_key(),
            book_title="Design Patterns",
            author="GoF",
            model="cloud",
            calibre_id=1234,
        )
        self._bl._append_report_chunk(
            book_key=self._book_key(),
            chunk_label="[001]",
            n_deposited=5,
            summary="first run chunk",
            is_error=False,
            model_tag="cloud",
        )
        # Second run: write header again — should reset file
        self._bl._write_report_header(
            book_key=self._book_key(),
            book_title="Design Patterns",
            author="GoF",
            model="cloud",
            calibre_id=1234,
        )
        content = self._report_file().read_text()
        # Old chunk line should be gone after header reset
        self.assertNotIn("first run chunk", content)


if __name__ == "__main__":
    unittest.main()
