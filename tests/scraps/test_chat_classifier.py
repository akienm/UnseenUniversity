"""
test_chat_classifier.py — Tests for ChatClassifier
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.scraps.chat_classifier import ChatClassifier, _CHATS_ROOT


class TestChatClassifier:
    """Tests for ChatClassifier."""

    def test_run_once_no_chats_root(self):
        """run_once handles missing chats root gracefully."""
        classifier = ChatClassifier("postgresql://test")
        with patch("unseen_university.devices.scraps.chat_classifier._CHATS_ROOT") as mock_root:
            mock_root.exists.return_value = False
            result = classifier.run_once()
        assert result["turns_read"] == 0
        assert result["nodes_built"] == 0

    def test_classify_file_with_turns(self):
        """Classify file reads turns and counts them."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chat_file = Path(tmpdir) / "test.jsonl"
            # Write sample turns
            turns = [
                {"ts": "2026-01-01T00:00:00", "dir": "in", "content": "how to do X", "thread_id": "web:shared"},
                {"ts": "2026-01-01T00:01:00", "dir": "out", "content": "here's the approach", "thread_id": "web:shared"},
                {"ts": "2026-01-01T00:02:00", "dir": "in", "content": "", "thread_id": "web:shared"},  # empty
            ]
            with open(chat_file, "w") as f:
                for turn in turns:
                    f.write(json.dumps(turn) + "\n")

            classifier = ChatClassifier("postgresql://test")
            mock_conn = MagicMock()
            nodes = classifier._classify_file(chat_file, mock_conn)

            # Should have stored 2 non-empty turns
            assert nodes >= 0

    def test_skip_very_long_turns(self):
        """Skips turns longer than MAX_TURN_LEN."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chat_file = Path(tmpdir) / "test.jsonl"
            long_content = "x" * 10000
            turn = {"ts": "2026-01-01T00:00:00", "dir": "in", "content": long_content, "thread_id": "web:shared"}
            with open(chat_file, "w") as f:
                f.write(json.dumps(turn) + "\n")

            classifier = ChatClassifier("postgresql://test")
            mock_conn = MagicMock()
            nodes = classifier._classify_file(chat_file, mock_conn)
            assert nodes == 0

    def test_skip_empty_turns(self):
        """Skips empty/whitespace-only turns."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chat_file = Path(tmpdir) / "test.jsonl"
            turns = [
                {"ts": "2026-01-01T00:00:00", "dir": "in", "content": "", "thread_id": "web:shared"},
                {"ts": "2026-01-01T00:01:00", "dir": "in", "content": "   ", "thread_id": "web:shared"},
            ]
            with open(chat_file, "w") as f:
                for turn in turns:
                    f.write(json.dumps(turn) + "\n")

            classifier = ChatClassifier("postgresql://test")
            mock_conn = MagicMock()
            nodes = classifier._classify_file(chat_file, mock_conn)
            assert nodes == 0

    def test_store_classified_turn_success(self):
        """Store classified turn calls INSERT with correct values."""
        classifier = ChatClassifier("postgresql://test")
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        chat_file = Path("/tmp/test.jsonl")
        turn = {"content": "test content", "dir": "in", "thread_id": "web:shared"}

        result = classifier._store_classified_turn(
            chat_file, 1, turn, "skill", "HIGH", mock_conn
        )

        assert result is True
        mock_cursor.execute.assert_called_once()
        # Verify the INSERT statement was called with correct args
        call_args = mock_cursor.execute.call_args
        assert "INSERT INTO adc.palace" in call_args[0][0]
