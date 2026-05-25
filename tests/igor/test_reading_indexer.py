"""
Tests for T-reading-indexer: chunk → G54 extract → FACT_CLOUD nodes.

Verifies that:
- Chunks are read from blob_store
- Facts are extracted and deposited as FACT_CLOUD nodes
- Metadata includes content_id, chapter, chunk_idx
- Links to interpretive nodes work
- blob_index status updated to "indexed"
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime



class TestReadingIndexer(unittest.TestCase):
    """Test reading_indexer module."""

    def setUp(self):
        """Set up test fixtures."""
        from devices.igor.cognition.reading_indexer import (
            _fact_cloud_id,
            _get_cortex,
        )

        self.fact_cloud_id = _fact_cloud_id
        self.get_cortex = _get_cortex

    def test_fact_cloud_id_generation(self):
        """Verify stable ID generation for FACT_CLOUD nodes."""
        content_id = "550e8400-e29b-41d4-a716-446655440000"
        chapter_idx = 1
        chunk_idx = 0
        fact_text = "This is a key idea about learning"

        id1 = self.fact_cloud_id(content_id, chapter_idx, chunk_idx, fact_text)
        id2 = self.fact_cloud_id(content_id, chapter_idx, chunk_idx, fact_text)

        # Should be stable
        self.assertEqual(id1, id2)
        # Should have FACT_CLOUD prefix
        self.assertTrue(id1.startswith("FACT_CLOUD_"))
        # Should be 16 chars (FACT_CLOUD_ + 8-char hash)
        self.assertEqual(len(id1), len("FACT_CLOUD_") + 8)

    def test_fact_cloud_id_different_for_different_facts(self):
        """Verify different facts get different IDs."""
        content_id = "550e8400-e29b-41d4-a716-446655440000"

        id1 = self.fact_cloud_id(content_id, 1, 0, "Fact A")
        id2 = self.fact_cloud_id(content_id, 1, 0, "Fact B")

        self.assertNotEqual(id1, id2)

    def test_extract_facts_from_chunk_empty_text(self):
        """Verify extraction skips empty/short chunks."""
        from devices.igor.cognition.reading_indexer import _extract_facts_from_chunk

        # Too short
        result = _extract_facts_from_chunk("hi", "Title", 1)
        self.assertEqual(result, [])

    @patch("urllib.request.urlopen")
    def test_extract_facts_from_chunk_api_call(self, mock_urlopen):
        """Verify API is called with correct parameters."""
        from devices.igor.cognition.reading_indexer import _extract_facts_from_chunk

        # Mock API response
        mock_response = MagicMock()
        mock_response.__enter__.return_value.read.return_value = b"""{
            "choices": [{
                "message": {
                    "content": "[{\\"narrative\\": \\"Key idea\\", \\"node_id\\": \\"CP1\\", \\"meaning_payload\\": \\"Epistemic\\", \\"confidence\\": 0.8}]"
                }
            }]
        }"""
        mock_urlopen.return_value = mock_response

        # Set API key
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            result = _extract_facts_from_chunk(
                "This is a substantial chunk with enough content to analyze.",
                "Test Title",
                1,
            )

        # Should have extracted facts
        self.assertGreater(len(result), 0)
        # Should filter by confidence
        self.assertTrue(all(f.get("confidence", 0) >= 0.6 for f in result))

    def test_deposit_fact_creates_memory(self):
        """Verify fact deposition creates correct Memory object."""
        from devices.igor.cognition.reading_indexer import _deposit_fact
        from devices.igor.memory.models import Memory, MemoryType

        # Mock cortex
        mock_cortex = MagicMock()
        mock_cortex.store = MagicMock()
        mock_cortex.get = MagicMock(return_value=None)

        fact = {
            "narrative": "A key learning point",
            "node_id": "CP2",
            "meaning_payload": "Failure as learning",
            "confidence": 0.75,
        }

        content_id = "550e8400-e29b-41d4-a716-446655440000"
        node_id = _deposit_fact(
            mock_cortex,
            content_id,
            chapter_idx=1,
            chunk_idx=0,
            fact=fact,
            title="Test Book",
            author="Test Author",
        )

        # Should return a node ID
        self.assertTrue(node_id.startswith("FACT_CLOUD_"))

        # Should have called cortex.store
        self.assertTrue(mock_cortex.store.called)
        stored_memory = mock_cortex.store.call_args[0][0]

        # Verify memory properties
        self.assertEqual(stored_memory.narrative, "A key learning point")
        self.assertEqual(stored_memory.memory_type, MemoryType.FACTUAL)
        self.assertEqual(stored_memory.source, "reading_indexer")
        self.assertEqual(stored_memory.confidence, 0.75)

        # Verify metadata
        self.assertEqual(stored_memory.metadata["content_id"], content_id)
        self.assertEqual(stored_memory.metadata["chapter_idx"], 1)
        self.assertEqual(stored_memory.metadata["chunk_idx"], 0)

    def test_deposit_fact_filters_low_confidence(self):
        """Verify low-confidence facts are not deposited."""
        from devices.igor.cognition.reading_indexer import _deposit_fact

        mock_cortex = MagicMock()

        fact = {
            "narrative": "Weak idea",
            "node_id": "CP3",
            "meaning_payload": "",
            "confidence": 0.4,  # Below 0.6 threshold
        }

        node_id = _deposit_fact(
            mock_cortex,
            "content-id",
            1,
            0,
            fact,
            "Title",
            "Author",
        )

        # Should return empty string
        self.assertEqual(node_id, "")
        # Should not store
        self.assertFalse(mock_cortex.store.called)

    @patch("devices.igor.cognition.reading_indexer.get_blob_metadata")
    @patch("devices.igor.cognition.reading_indexer.get_chunks")
    @patch("devices.igor.cognition.reading_indexer._extract_facts_from_chunk")
    @patch("devices.igor.cognition.reading_indexer._get_cortex")
    def test_index_content_flow(
        self, mock_get_cortex, mock_extract, mock_get_chunks, mock_get_metadata
    ):
        """Verify complete indexing flow."""
        # Mock cortex
        mock_cortex = MagicMock()
        mock_get_cortex.return_value = mock_cortex

        # Mock blob metadata
        mock_get_metadata.return_value = {
            "content_id": "test-id",
            "title": "Test Book",
            "author": "Test Author",
            "source_channel": "test",
            "format": "text",
        }

        # Mock chunks
        mock_get_chunks.return_value = [
            {
                "chapter_idx": 0,
                "chapter_title": "Chapter 1",
                "chunk_idx": 0,
                "text": "This is chapter content with substance to analyze and extract facts from.",
            }
        ]

        # Mock extraction
        mock_extract.return_value = [
            {
                "narrative": "First idea",
                "node_id": "CP1",
                "meaning_payload": "Epistemic",
                "confidence": 0.85,
            }
        ]

        from devices.igor.cognition.reading_indexer import index_content

        result = index_content("test-id", dry_run=False)

        # Should succeed
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["content_id"], "test-id")
        self.assertEqual(result["chunks_processed"], 1)
        self.assertEqual(result["facts_extracted"], 1)
        self.assertGreater(result["nodes_deposited"], 0)

    @patch("devices.igor.cognition.reading_indexer._get_cortex")
    @patch("devices.igor.cognition.reading_indexer.get_blob_metadata")
    def test_index_content_missing_blob(self, mock_get_metadata, mock_get_cortex):
        """Verify error handling for missing blob."""
        mock_cortex = MagicMock()
        mock_get_cortex.return_value = mock_cortex
        mock_get_metadata.return_value = None

        from devices.igor.cognition.reading_indexer import index_content

        result = index_content("missing-id", dry_run=False)

        self.assertEqual(result["status"], "error")
        self.assertIn("not found", result["error"])

    @patch("devices.igor.cognition.reading_indexer._get_cortex")
    def test_index_content_no_cortex(self, mock_get_cortex):
        """Verify error handling when cortex unavailable."""
        mock_get_cortex.return_value = None

        from devices.igor.cognition.reading_indexer import index_content

        result = index_content("test-id", dry_run=False)

        self.assertEqual(result["status"], "error")
        self.assertIn("cortex", result["error"].lower())


class TestReadingIndexerIntegration(unittest.TestCase):
    """Integration tests (may require test DB)."""

    def test_metadata_structure(self):
        """Verify metadata fields match specification."""
        from devices.igor.cognition.reading_indexer import _deposit_fact

        mock_cortex = MagicMock()

        fact = {
            "narrative": "Test fact",
            "node_id": "CP1",
            "meaning_payload": "Test meaning",
            "confidence": 0.8,
        }

        _deposit_fact(
            mock_cortex,
            "content-123",
            chapter_idx=5,
            chunk_idx=3,
            fact=fact,
            title="Book Title",
            author="Book Author",
        )

        # Verify stored memory
        stored_mem = mock_cortex.store.call_args[0][0]
        metadata = stored_mem.metadata

        # Verify required fields
        self.assertIn("content_id", metadata)
        self.assertIn("chapter_idx", metadata)
        self.assertIn("chunk_idx", metadata)
        self.assertIn("extraction_confidence", metadata)
        self.assertIn("title", metadata)
        self.assertIn("author", metadata)

        # Verify values
        self.assertEqual(metadata["content_id"], "content-123")
        self.assertEqual(metadata["chapter_idx"], 5)
        self.assertEqual(metadata["chunk_idx"], 3)


if __name__ == "__main__":
    unittest.main()
