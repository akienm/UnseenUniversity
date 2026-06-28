"""
test_reply_gap_detector.py — T-any-thoughts-habit-failure (#468)

Tests for reply-prod detection, gap finding, and flagging.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.cognition.reply_gap_detector import (  # noqa: E402
    ReplyGap,
    detect_and_flag,
    find_reply_gap,
    flag_reply_gap,
    is_reply_prod,
)


class TestIsReplyProd:
    def test_any_thoughts(self):
        assert is_reply_prod("any thoughts?") is True

    def test_any_thoughts_capitalized(self):
        assert is_reply_prod("Any thoughts?") is True

    def test_what_do_you_think(self):
        assert is_reply_prod("what do you think?") is True

    def test_you_still_there(self):
        assert is_reply_prod("you still there?") is True

    def test_igor_question(self):
        assert is_reply_prod("igor?") is True

    def test_hello_question(self):
        assert is_reply_prod("hello?") is True

    def test_well_question(self):
        assert is_reply_prod("well?") is True

    def test_still_thinking(self):
        assert is_reply_prod("still thinking?") is True

    def test_normal_message_not_prod(self):
        assert is_reply_prod("tell me about the weather") is False

    def test_empty_not_prod(self):
        assert is_reply_prod("") is False

    def test_long_message_not_prod(self):
        assert is_reply_prod("x" * 300) is False

    def test_any_thoughts_in_sentence(self):
        assert is_reply_prod("do you have any thoughts on this?") is True


class TestFindReplyGap:
    def test_finds_gap_when_user_turn_unanswered(self):
        cortex = MagicMock()
        cortex.read_ring_memory.return_value = [
            {
                "category": "user_turn",
                "content": "USER_INPUT: tell me about X",
                "created_at": "t1",
            },
            {
                "category": "user_turn",
                "content": "USER_INPUT: any thoughts?",
                "created_at": "t2",
            },
        ]
        gap = find_reply_gap(cortex)
        assert gap is not None
        assert "tell me about X" in gap.user_input

    def test_no_gap_when_reply_exists(self):
        cortex = MagicMock()
        cortex.read_ring_memory.return_value = [
            {
                "category": "user_turn",
                "content": "USER_INPUT: tell me about X",
                "created_at": "t1",
            },
            {
                "category": "habit_trace",
                "content": "HABIT_EXEC|some_habit",
                "created_at": "t2",
            },
            {
                "category": "user_turn",
                "content": "USER_INPUT: thanks",
                "created_at": "t3",
            },
        ]
        gap = find_reply_gap(cortex)
        assert gap is None

    def test_no_gap_when_think_trace_exists(self):
        cortex = MagicMock()
        cortex.read_ring_memory.return_value = [
            {
                "category": "user_turn",
                "content": "USER_INPUT: question",
                "created_at": "t1",
            },
            {
                "category": "think_trace",
                "content": "THINK|reasoning",
                "created_at": "t2",
            },
            {
                "category": "user_turn",
                "content": "USER_INPUT: next",
                "created_at": "t3",
            },
        ]
        gap = find_reply_gap(cortex)
        assert gap is None

    def test_no_gap_on_empty_ring(self):
        cortex = MagicMock()
        cortex.read_ring_memory.return_value = []
        gap = find_reply_gap(cortex)
        assert gap is None

    def test_handles_read_ring_exception(self):
        cortex = MagicMock()
        cortex.read_ring_memory.side_effect = RuntimeError("db")
        gap = find_reply_gap(cortex)
        assert gap is None

    def test_strips_user_input_prefix(self):
        cortex = MagicMock()
        cortex.read_ring_memory.return_value = [
            {
                "category": "user_turn",
                "content": "USER_INPUT: what is this?",
                "created_at": "t1",
            },
            {
                "category": "user_turn",
                "content": "USER_INPUT: hello?",
                "created_at": "t2",
            },
        ]
        gap = find_reply_gap(cortex)
        assert gap is not None
        assert not gap.user_input.startswith("USER_INPUT:")


class TestFlagReplyGap:
    def test_deposits_episodic_memory(self):
        cortex = MagicMock()
        stored = MagicMock()
        stored.id = "GAP_001"
        cortex.store.return_value = stored

        gap = ReplyGap(
            user_input="tell me about X",
            ring_category="user_turn",
            timestamp="2026-04-16T12:00:00Z",
            turns_ago=3,
        )
        result = flag_reply_gap(cortex, gap)
        assert result == "GAP_001"
        cortex.store.assert_called_once()
        mem = cortex.store.call_args[0][0]
        assert "REPLY_GAP" in mem.narrative
        assert mem.metadata["reply_gap"] is True
        assert mem.metadata["needs_sleep_review"] is True

    def test_handles_store_failure(self):
        cortex = MagicMock()
        cortex.store.side_effect = RuntimeError("db")
        gap = ReplyGap(
            user_input="test",
            ring_category="user_turn",
            timestamp="t",
            turns_ago=1,
        )
        result = flag_reply_gap(cortex, gap)
        assert result is None


class TestDetectAndFlag:
    def test_full_pipeline(self):
        cortex = MagicMock()
        cortex.read_ring_memory.return_value = [
            {
                "category": "user_turn",
                "content": "USER_INPUT: explain this",
                "created_at": "t1",
            },
            {
                "category": "user_turn",
                "content": "USER_INPUT: any thoughts?",
                "created_at": "t2",
            },
        ]
        stored = MagicMock()
        stored.id = "GAP_002"
        cortex.store.return_value = stored
        cortex.twm_push.return_value = 1

        result = detect_and_flag(cortex, "any thoughts?")
        assert result == "GAP_002"
        cortex.twm_push.assert_called_once()

    def test_returns_none_for_non_prod(self):
        cortex = MagicMock()
        result = detect_and_flag(cortex, "tell me about the weather")
        assert result is None
        cortex.read_ring_memory.assert_not_called()

    def test_returns_none_when_no_gap(self):
        cortex = MagicMock()
        cortex.read_ring_memory.return_value = [
            {"category": "user_turn", "content": "USER_INPUT: hi", "created_at": "t1"},
            {"category": "habit_trace", "content": "replied", "created_at": "t2"},
        ]
        result = detect_and_flag(cortex, "any thoughts?")
        assert result is None
