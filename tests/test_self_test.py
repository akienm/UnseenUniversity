"""
Tests for T-self-test: directed consolidation via Q&A after reading.

Verifies that:
- consolidate_content() processes content through Q&A testing
- Questions are generated and Igor attempts graph-based answers
- Answers are graded and edges are strengthened
- blob_index status is updated to "tested"
- Test results are logged correctly
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))


class TestSelfTest(unittest.TestCase):
    """Test self_test module."""

    def setUp(self):
        """Set up test fixtures."""
        from wild_igor.igor.cognition.self_test import (
            _get_instance_dir,
            _get_blob_index_path,
            _get_test_log_path,
            _update_blob_index_status,
            _log_test_result,
            _strengthen_edges_from_answer,
            _igor_answer_from_graph,
            consolidate_content,
        )

        self.get_instance_dir = _get_instance_dir
        self.get_blob_index_path = _get_blob_index_path
        self.get_test_log_path = _get_test_log_path
        self.update_blob_index_status = _update_blob_index_status
        self.log_test_result = _log_test_result
        self.strengthen_edges_from_answer = _strengthen_edges_from_answer
        self.igor_answer_from_graph = _igor_answer_from_graph
        self.consolidate_content = consolidate_content

    def test_get_instance_dir(self):
        """Verify instance directory path generation."""
        with patch.dict(os.environ, {"IGOR_DB_PATH": "/tmp/test.db"}):
            instance_dir = self.get_instance_dir()
            self.assertEqual(instance_dir, Path("/tmp"))

    def test_get_blob_index_path(self):
        """Verify blob_index.json path."""
        with patch.dict(os.environ, {"IGOR_DB_PATH": "/tmp/test.db"}):
            path = self.get_blob_index_path()
            self.assertEqual(path, Path("/tmp/blob_index.json"))

    def test_get_test_log_path(self):
        """Verify test log path."""
        with patch.dict(os.environ, {"IGOR_DB_PATH": "/tmp/test.db"}):
            path = self.get_test_log_path()
            self.assertEqual(path, Path("/tmp/self_test_log.jsonl"))

    def test_update_blob_index_status_new_entry(self):
        """Test creating new blob_index entry."""
        with patch.dict(os.environ, {"IGOR_DB_PATH": "/tmp/test.db"}):
            with patch("pathlib.Path.exists", return_value=False):
                with patch("pathlib.Path.write_text") as mock_write:
                    self.update_blob_index_status("test-id-123", "tested")

                    # Verify write was called
                    mock_write.assert_called_once()
                    written_data = json.loads(mock_write.call_args[0][0])
                    self.assertIn("test-id-123", written_data)
                    self.assertEqual(written_data["test-id-123"]["status"], "tested")

    def test_update_blob_index_status_existing_entry(self):
        """Test updating existing blob_index entry."""
        existing_index = {
            "test-id-123": {
                "title": "Test Content",
                "status": "indexed",
            }
        }

        with patch.dict(os.environ, {"IGOR_DB_PATH": "/tmp/test.db"}):
            with patch("pathlib.Path.exists", return_value=True):
                with patch(
                    "pathlib.Path.read_text", return_value=json.dumps(existing_index)
                ):
                    with patch("pathlib.Path.write_text") as mock_write:
                        self.update_blob_index_status("test-id-123", "tested")

                        written_data = json.loads(mock_write.call_args[0][0])
                        self.assertEqual(
                            written_data["test-id-123"]["status"], "tested"
                        )
                        # Title should be preserved
                        self.assertEqual(
                            written_data["test-id-123"]["title"], "Test Content"
                        )

    def test_log_test_result(self):
        """Test logging test results to JSONL."""
        with patch.dict(os.environ, {"IGOR_DB_PATH": "/tmp/test.db"}):
            with patch("builtins.open", create=True) as mock_file:
                mock_file.return_value.__enter__ = MagicMock()
                mock_file.return_value.__exit__ = MagicMock(return_value=False)

                self.log_test_result(
                    content_id="test-content-123",
                    chapter_idx=0,
                    questions=["What is X?", "How does Y work?"],
                    answers=["X is...", "Y works by..."],
                    grades=["correct", "partial"],
                    edges_updated=5,
                    miss_count=0,
                )

                # Verify write was called
                self.assertTrue(
                    mock_file.return_value.__enter__.return_value.write.called
                )

    def test_strengthen_edges_from_answer_miss(self):
        """Test edge strengthening for missed answer."""
        mock_wg = MagicMock()

        result = self.strengthen_edges_from_answer(
            wg=mock_wg,
            question="What is learning?",
            grade="miss",
            boost=0.02,
        )

        # Should have called reinforce_text with 0.02 boost
        mock_wg.reinforce_text.assert_called_once()
        call_args = mock_wg.reinforce_text.call_args
        self.assertEqual(call_args[1]["boost"], 0.02)

        # Should return number of edges updated
        self.assertGreater(result, 0)

    def test_strengthen_edges_from_answer_partial(self):
        """Test edge strengthening for partial answer."""
        mock_wg = MagicMock()

        result = self.strengthen_edges_from_answer(
            wg=mock_wg,
            question="What is learning?",
            grade="partial",
            boost=0.02,
        )

        # Should have called reinforce_text with 0.01 boost (partial)
        mock_wg.reinforce_text.assert_called_once()
        call_args = mock_wg.reinforce_text.call_args
        self.assertEqual(call_args[1]["boost"], 0.01)

    def test_strengthen_edges_from_answer_correct(self):
        """Test edge strengthening for correct answer."""
        mock_wg = MagicMock()

        result = self.strengthen_edges_from_answer(
            wg=mock_wg,
            question="What is learning?",
            grade="correct",
            boost=0.02,
        )

        # Should NOT strengthen for correct answer (already traversed)
        mock_wg.reinforce_text.assert_not_called()
        self.assertEqual(result, 0)

    def test_strengthen_edges_from_answer_no_word_graph(self):
        """Test edge strengthening when word graph is unavailable."""
        result = self.strengthen_edges_from_answer(
            wg=None,
            question="What is learning?",
            grade="miss",
            boost=0.02,
        )

        # Should gracefully return 0
        self.assertEqual(result, 0)

    def test_igor_answer_from_graph(self):
        """Test Igor answering via graph traversal."""
        mock_cortex = MagicMock()

        # Mock search results
        from wild_igor.igor.memory.models import Memory, MemoryType

        mock_memory = Memory(
            id="test-mem-1",
            narrative="This is relevant knowledge about learning",
            memory_type=MemoryType.FACTUAL,
        )
        mock_cortex.search.return_value = [mock_memory]

        answer = self.igor_answer_from_graph(mock_cortex, "What is learning?")

        # Should have called search
        mock_cortex.search.assert_called_once()
        call_args = mock_cortex.search.call_args
        self.assertEqual(call_args[0][0], "What is learning?")
        self.assertEqual(call_args[1]["depth"], "shallow")

        # Answer should contain the narrative
        self.assertIn("relevant knowledge", answer)

    def test_igor_answer_from_graph_no_results(self):
        """Test Igor answering when search returns no results."""
        mock_cortex = MagicMock()
        mock_cortex.search.return_value = []

        answer = self.igor_answer_from_graph(mock_cortex, "What is X?")

        # Should return empty string
        self.assertEqual(answer, "")

    def test_igor_answer_from_graph_no_cortex(self):
        """Test Igor answering when cortex is unavailable."""
        answer = self.igor_answer_from_graph(None, "What is learning?")

        # Should return empty string gracefully
        self.assertEqual(answer, "")

    @patch("wild_igor.igor.cognition.self_test._get_cortex")
    @patch("wild_igor.igor.cognition.self_test._get_word_graph")
    @patch("wild_igor.igor.cognition.self_test.get_blob_metadata")
    @patch("wild_igor.igor.cognition.self_test.get_chunks")
    def test_consolidate_content_no_cortex(
        self, mock_get_chunks, mock_metadata, mock_wg, mock_cortex
    ):
        """Test consolidate_content when cortex is unavailable."""
        mock_cortex.return_value = None

        self.consolidate_content("test-content-123")

        # Should log error and return early
        mock_metadata.assert_not_called()

    @patch("wild_igor.igor.cognition.self_test._get_cortex")
    @patch("wild_igor.igor.cognition.self_test._get_word_graph")
    @patch("wild_igor.igor.cognition.self_test.get_blob_metadata")
    @patch("wild_igor.igor.cognition.self_test.get_chunks")
    def test_consolidate_content_no_metadata(
        self, mock_get_chunks, mock_metadata, mock_wg, mock_cortex
    ):
        """Test consolidate_content when blob metadata is unavailable."""
        mock_cortex.return_value = MagicMock()
        mock_metadata.return_value = None

        self.consolidate_content("test-content-123")

        # Should log error and return early
        mock_get_chunks.assert_not_called()


if __name__ == "__main__":
    unittest.main()
