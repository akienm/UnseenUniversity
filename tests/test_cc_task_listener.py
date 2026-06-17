"""Tests for lab.claudecode.cc_task_listener."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from devlab.claudecode.cc_task_listener import (
    TaskListener,
    _parse_dispatch_msg,
)


class TestParseDispatchMsg:
    def test_parses_well_formed_message(self):
        msg = "GRANNY_DISPATCH|ticket=T-foo|worker=claude|size=S|tags=Platform"
        result = _parse_dispatch_msg(msg)
        assert result is not None
        assert result["ticket"] == "T-foo"
        assert result["worker"] == "claude"
        assert result["size"] == "S"

    def test_returns_none_for_non_dispatch_message(self):
        assert _parse_dispatch_msg("GRANNY_ACK|ticket=T-foo") is None
        assert _parse_dispatch_msg("hello world") is None

    def test_returns_none_when_no_ticket_field(self):
        assert _parse_dispatch_msg("GRANNY_DISPATCH|worker=claude") is None

    def test_handles_extra_fields(self):
        msg = (
            "GRANNY_DISPATCH|ticket=T-bar|title=Fix auth|size=M|tags=Database,Platform"
        )
        result = _parse_dispatch_msg(msg)
        assert result["ticket"] == "T-bar"


class TestTaskListenerPollOnce:
    def _listener(self):
        return TaskListener()

    def test_dispatches_granny_dispatch_message(self):
        messages = [
            {"id": 10, "content": "GRANNY_DISPATCH|ticket=T-abc|worker=claude|size=S"}
        ]
        dispatched = []

        def fake_dispatch(ticket_id):
            dispatched.append(ticket_id)
            return True

        with (
            patch(
                "devlab.claudecode.cc_task_listener._fetch_new_messages",
                return_value=messages,
            ),
            patch("devlab.claudecode.cc_task_listener._read_hwm", return_value=0),
            patch("devlab.claudecode.cc_task_listener._write_hwm"),
            patch(
                "devlab.claudecode.cc_task_listener._dispatch_ticket",
                side_effect=fake_dispatch,
            ),
            patch("devlab.claudecode.cc_task_listener._post_ack"),
        ):
            count = self._listener().poll_once()

        assert count == 1
        assert dispatched == ["T-abc"]

    def test_posts_ack_after_dispatch(self):
        messages = [
            {"id": 5, "content": "GRANNY_DISPATCH|ticket=T-xyz|worker=claude|size=M"}
        ]
        acked = []

        with (
            patch(
                "devlab.claudecode.cc_task_listener._fetch_new_messages",
                return_value=messages,
            ),
            patch("devlab.claudecode.cc_task_listener._read_hwm", return_value=0),
            patch("devlab.claudecode.cc_task_listener._write_hwm"),
            patch(
                "devlab.claudecode.cc_task_listener._dispatch_ticket", return_value=True
            ),
            patch(
                "devlab.claudecode.cc_task_listener._post_ack",
                side_effect=lambda tid, status: acked.append((tid, status)),
            ),
        ):
            self._listener().poll_once()

        assert len(acked) == 1
        assert acked[0] == ("T-xyz", "in_progress")

    def test_advances_high_water_mark(self):
        messages = [
            {"id": 7, "content": "GRANNY_DISPATCH|ticket=T-a|worker=claude|size=S"},
            {"id": 9, "content": "GRANNY_DISPATCH|ticket=T-b|worker=claude|size=S"},
        ]
        hwm_written = []

        with (
            patch(
                "devlab.claudecode.cc_task_listener._fetch_new_messages",
                return_value=messages,
            ),
            patch("devlab.claudecode.cc_task_listener._read_hwm", return_value=5),
            patch(
                "devlab.claudecode.cc_task_listener._write_hwm",
                side_effect=hwm_written.append,
            ),
            patch(
                "devlab.claudecode.cc_task_listener._dispatch_ticket", return_value=True
            ),
            patch("devlab.claudecode.cc_task_listener._post_ack"),
        ):
            self._listener().poll_once()

        assert hwm_written == [9]

    def test_skips_non_dispatch_messages(self):
        messages = [
            {"id": 3, "content": "hello from igor"},
            {"id": 4, "content": "GRANNY_ACK|ticket=T-done|status=in_progress"},
        ]

        with (
            patch(
                "devlab.claudecode.cc_task_listener._fetch_new_messages",
                return_value=messages,
            ),
            patch("devlab.claudecode.cc_task_listener._read_hwm", return_value=0),
            patch("devlab.claudecode.cc_task_listener._write_hwm"),
            patch("devlab.claudecode.cc_task_listener._dispatch_ticket") as mock_dispatch,
            patch("devlab.claudecode.cc_task_listener._post_ack"),
        ):
            count = self._listener().poll_once()

        assert count == 0
        mock_dispatch.assert_not_called()

    def test_posts_failed_ack_when_dispatch_fails(self):
        messages = [
            {"id": 11, "content": "GRANNY_DISPATCH|ticket=T-fail|worker=claude|size=S"}
        ]
        acked = []

        with (
            patch(
                "devlab.claudecode.cc_task_listener._fetch_new_messages",
                return_value=messages,
            ),
            patch("devlab.claudecode.cc_task_listener._read_hwm", return_value=0),
            patch("devlab.claudecode.cc_task_listener._write_hwm"),
            patch(
                "devlab.claudecode.cc_task_listener._dispatch_ticket", return_value=False
            ),
            patch(
                "devlab.claudecode.cc_task_listener._post_ack",
                side_effect=lambda tid, status: acked.append((tid, status)),
            ),
        ):
            count = self._listener().poll_once()

        assert count == 0
        assert acked[0][1] == "dispatch_failed"
