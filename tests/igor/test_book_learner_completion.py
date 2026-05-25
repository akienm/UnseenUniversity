"""
Tests for T-reading-completion-status: book_learner deposits EPISODIC
completion records so Igor can answer "did I finish X?".
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "lab"))


class TestDepositCompletionRecord(unittest.TestCase):
    """_deposit_completion_record stores an EPISODIC node in cortex."""

    def setUp(self):
        from claudecode.book_learner import _deposit_completion_record
        self.fn = _deposit_completion_record

    def _make_cortex(self):
        cortex = MagicMock()
        cortex.store = MagicMock()
        return cortex

    def test_complete_status_narrative(self):
        """Narrative says 'completed' for status=complete."""
        cortex = self._make_cortex()
        self.fn(
            cortex=cortex,
            book_title="Making Money",
            author="Terry Pratchett",
            book_key="Making Money|3023",
            calibre_id=3023,
            total_sentences=5000,
            chunks_processed=333,
            total_deposited=120,
            status="complete",
        )
        cortex.store.assert_called_once()
        mem = cortex.store.call_args[0][0]
        self.assertIn("Making Money", mem.narrative)
        self.assertIn("completed", mem.narrative)
        self.assertIn("Status: complete", mem.narrative)

    def test_partial_status_narrative(self):
        """Narrative says 'partially read' for status=partial."""
        cortex = self._make_cortex()
        self.fn(
            cortex=cortex,
            book_title="Thinking Fast and Slow",
            author="Kahneman",
            book_key="Thinking Fast and Slow|",
            calibre_id=None,
            total_sentences=8000,
            chunks_processed=50,
            total_deposited=20,
            status="partial",
        )
        mem = cortex.store.call_args[0][0]
        self.assertIn("partially read", mem.narrative)
        self.assertIn("Status: partial", mem.narrative)

    def test_episodic_memory_type(self):
        """Completion node is EPISODIC."""
        from devices.igor.memory.models import MemoryType
        cortex = self._make_cortex()
        self.fn(
            cortex=cortex,
            book_title="Test Book",
            author="Test Author",
            book_key="Test Book|",
            calibre_id=None,
            total_sentences=100,
            chunks_processed=7,
            total_deposited=3,
            status="partial",
        )
        mem = cortex.store.call_args[0][0]
        self.assertEqual(mem.memory_type, MemoryType.EPISODIC)

    def test_node_id_is_deterministic(self):
        """Same book_key always produces the same node id (re-runs overwrite)."""
        cortex1 = self._make_cortex()
        cortex2 = self._make_cortex()
        kwargs = dict(
            book_title="Dune",
            author="Herbert",
            book_key="Dune|42",
            calibre_id=42,
            total_sentences=9000,
            chunks_processed=600,
            total_deposited=200,
            status="complete",
        )
        self.fn(cortex=cortex1, **kwargs)
        self.fn(cortex=cortex2, **kwargs)
        id1 = cortex1.store.call_args[0][0].id
        id2 = cortex2.store.call_args[0][0].id
        self.assertEqual(id1, id2)
        self.assertTrue(id1.startswith("READING_"))

    def test_metadata_fields(self):
        """Metadata includes all required fields."""
        cortex = self._make_cortex()
        self.fn(
            cortex=cortex,
            book_title="Neuromancer",
            author="Gibson",
            book_key="Neuromancer|99",
            calibre_id=99,
            total_sentences=4000,
            chunks_processed=267,
            total_deposited=80,
            status="complete",
        )
        meta = cortex.store.call_args[0][0].metadata
        self.assertEqual(meta["book_title"], "Neuromancer")
        self.assertEqual(meta["status"], "complete")
        self.assertEqual(meta["chunks_processed"], 267)
        self.assertEqual(meta["total_deposited"], 80)
        self.assertEqual(meta["calibre_id"], 99)
        self.assertIn("finished_at", meta)

    def test_no_calibre_id_omitted_from_meta(self):
        """calibre_id absent from metadata when None."""
        cortex = self._make_cortex()
        self.fn(
            cortex=cortex,
            book_title="Web Book",
            author="Unknown",
            book_key="Web Book|",
            calibre_id=None,
            total_sentences=500,
            chunks_processed=33,
            total_deposited=10,
            status="partial",
        )
        meta = cortex.store.call_args[0][0].metadata
        self.assertNotIn("calibre_id", meta)


if __name__ == "__main__":
    unittest.main()
