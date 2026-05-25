"""
test_thread_anchor_dedup.py — T-igor-input-echo-thread-history

Verifies _strip_thread_prefix peels off the synthetic thread-context block
from user_input before it's stored on a thread anchor — preventing the
recursive nesting Igor self-reported on 2026-04-18.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


from devices.igor.tools.thread_anchor import _strip_thread_prefix


class TestStripThreadPrefix(unittest.TestCase):
    def test_plain_text_unchanged(self):
        text = "hello how are you"
        self.assertEqual(_strip_thread_prefix(text), text)

    def test_web_message_without_thread_context_unchanged(self):
        text = "[Web message from akien]: run the tests"
        self.assertEqual(_strip_thread_prefix(text), text)

    def test_strips_thread_context_from_web_message(self):
        text = (
            "[Thread context — recent exchanges in this channel:]\n"
            "  User: testing\n"
            "  Igor: Testing back.\n"
            "\n"
            "[Web message from akien]: run the tests"
        )
        self.assertEqual(
            _strip_thread_prefix(text),
            "[Web message from akien]: run the tests",
        )

    def test_strips_context_nested_into_prior_prefix(self):
        # The exact pattern Igor flagged — a prior prefix embedded in what
        # looks like a user line, followed by the actual current message.
        nested = (
            "[Thread context — recent exchanges in this channel:]\n"
            "  User: [Thread context — recent exchanges in this channel:]\n"
            "  User: hello?\n"
            "  Igor: Hi.\n"
            "\n"
            "[Discord message from akien in #general "
            "on guild, channel_id=0]: what's up"
        )
        out = _strip_thread_prefix(nested)
        # Should anchor on the LAST [Discord message from ...] tag and return
        # from there — stripping all nested prior-context.
        self.assertTrue(out.startswith("[Discord message from akien"))
        self.assertIn("what's up", out)
        self.assertNotIn("Thread context", out)

    def test_handles_cc_prefix_tag(self):
        text = (
            "[Thread context — recent exchanges in this channel:]\n"
            "  User: hi\n"
            "\n"
            "CC: do the thing"
        )
        self.assertEqual(_strip_thread_prefix(text), "CC: do the thing")

    def test_marker_present_but_no_message_tag_is_passthrough(self):
        # Fail-open: if we can't identify where the real message starts,
        # return text unchanged rather than silently cutting it off.
        text = "[Thread context — recent exchanges in this channel:]\n  blah"
        self.assertEqual(_strip_thread_prefix(text), text)


if __name__ == "__main__":
    unittest.main()
