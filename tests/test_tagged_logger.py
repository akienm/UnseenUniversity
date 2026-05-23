"""Tests for TaggedLogger proxy."""

from __future__ import annotations

import pytest
from diagnostic_base.tagged_logger import TaggedLogger, _TagBoundLogger


class TestTaggedLogger:
    def test_getattr_returns_tag_bound_logger(self):
        tl = TaggedLogger()
        child = tl.perf
        assert isinstance(child, _TagBoundLogger)
        assert child._tag == "perf"

    def test_nested_tag_combines(self):
        tl = TaggedLogger()
        child = tl.perf.verbose
        assert child._tag == "perf.verbose"

    def test_call_is_info_shorthand(self, capfd):
        # Just verify it doesn't raise — loguru output goes to stderr
        tl = TaggedLogger()
        tl.perf("some message")

    def test_standard_levels_do_not_raise(self):
        tl = TaggedLogger()
        tl.debug("d")
        tl.info("i")
        tl.warning("w")
        tl.error("e")

    def test_private_attr_raises_attribute_error(self):
        tl = TaggedLogger()
        with pytest.raises(AttributeError):
            _ = tl.__nonexistent__

    def test_bind_returns_tagged_logger(self):
        tl = TaggedLogger()
        bound = tl.bind(device_id="x")
        assert isinstance(bound, TaggedLogger)
