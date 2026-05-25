"""tests/test_cc_inbox_producer.py — Igor-side CC inbox producer hooks.

Covers:
- cc_inbox_bridge.post_to_cc_inbox() is non-fatal on import failure
- cc_inbox_bridge.post_to_cc_inbox() forwards args to underlying append()
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# ── cc_inbox_bridge.post_to_cc_inbox ────────────────────────────────────────


class TestPostToCcInboxBridge:
    def test_forwards_args_to_append(self):
        from devices.igor.cognition import cc_inbox_bridge

        with patch("lab.claudecode.cc_inbox.append") as mock_append:
            cc_inbox_bridge.post_to_cc_inbox(
                kind="test_kind",
                summary="test summary",
                body="test body",
                ticket_id="T-test",
                urgency="high",
                response_expected=True,
            )

        mock_append.assert_called_once()
        _, kwargs = mock_append.call_args
        assert kwargs["kind"] == "test_kind"
        assert kwargs["summary"] == "test summary"
        assert kwargs["body"] == "test body"
        assert kwargs["ticket_id"] == "T-test"
        assert kwargs["urgency"] == "high"
        assert kwargs["response_expected"] is True

    def test_non_fatal_on_append_exception(self):
        from devices.igor.cognition import cc_inbox_bridge

        with patch("lab.claudecode.cc_inbox.append", side_effect=RuntimeError("boom")):
            # Must not raise
            cc_inbox_bridge.post_to_cc_inbox(kind="k", summary="s")

    def test_default_urgency_normal(self):
        from devices.igor.cognition import cc_inbox_bridge

        with patch("lab.claudecode.cc_inbox.append") as mock_append:
            cc_inbox_bridge.post_to_cc_inbox(kind="k", summary="s")

        _, kwargs = mock_append.call_args
        assert kwargs["urgency"] == "normal"
        assert kwargs["response_expected"] is False
