"""
test_memory_comment.py — T-memory-metadata-comment-convention

Tests for Memory.add_comment helper and Memory.comment property.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.memory.models import Memory, MemoryType  # noqa: E402


def _mem(narrative="x") -> Memory:
    return Memory(narrative=narrative, memory_type=MemoryType.FACTUAL)


class TestAddComment:
    def test_first_comment_sets_field(self):
        m = _mem()
        m.add_comment("the why")
        assert m.metadata["comment"] == "the why"

    def test_second_comment_appends_with_separator(self):
        m = _mem()
        m.add_comment("first")
        m.add_comment("second")
        assert m.metadata["comment"] == "first | second"

    def test_three_comments_chain(self):
        m = _mem()
        m.add_comment("a")
        m.add_comment("b")
        m.add_comment("c")
        assert m.metadata["comment"] == "a | b | c"

    def test_empty_text_is_noop(self):
        m = _mem()
        m.add_comment("")
        assert "comment" not in m.metadata

    def test_none_text_is_noop(self):
        m = _mem()
        m.add_comment(None)  # type: ignore[arg-type]
        assert "comment" not in m.metadata

    def test_whitespace_only_is_noop(self):
        m = _mem()
        m.add_comment("   ")
        assert "comment" not in m.metadata

    def test_strips_leading_trailing_whitespace(self):
        m = _mem()
        m.add_comment("  hello  ")
        assert m.metadata["comment"] == "hello"

    def test_does_not_clobber_other_metadata(self):
        m = _mem()
        m.metadata["other"] = "value"
        m.add_comment("note")
        assert m.metadata["other"] == "value"
        assert m.metadata["comment"] == "note"


class TestCommentProperty:
    def test_unset_returns_empty_string(self):
        m = _mem()
        assert m.comment == ""

    def test_returns_field_value(self):
        m = _mem()
        m.add_comment("hello")
        assert m.comment == "hello"

    def test_handles_none_value(self):
        m = _mem()
        m.metadata["comment"] = None
        assert m.comment == ""
