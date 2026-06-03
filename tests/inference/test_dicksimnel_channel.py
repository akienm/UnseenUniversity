"""Tests for DickSimnel lifecycle channel events (T-dicksimnel-channel)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest


def _device():
    from devices.dicksimnel.device import DickSimnelDevice
    d = DickSimnelDevice()
    d._shim = MagicMock()
    d._shim.is_blocked.return_value = False
    return d


def _mock_subprocess(ticket_data: dict) -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = json.dumps(ticket_data)
    return m


# ── _channel_event ────────────────────────────────────────────────────────────


def test_channel_event_calls_post_to_channel():
    d = _device()
    with patch("unseen_university.channel.post_to_channel") as mock_post:
        d._channel_event("DICKSIMNEL_WORKING ticket=T-x title='test'")
    mock_post.assert_called_once_with(
        "DICKSIMNEL_WORKING ticket=T-x title='test'",
        author="dicksimnel",
        channel="shared",
    )


def test_channel_event_swallows_exception(caplog):
    d = _device()
    with patch("unseen_university.channel.post_to_channel", side_effect=RuntimeError("no channel")):
        d._channel_event("DICKSIMNEL_WORKING ticket=T-x title='test'")
    # must not raise; warning logged
    assert any("channel post failed" in r.message for r in caplog.records)


# ── _claim_next_ticket posts WORKING ─────────────────────────────────────────


def test_claim_posts_working():
    d = _device()
    ticket = {"id": "T-test", "title": "Fix the thing", "status": "in_progress"}
    with patch("subprocess.run", return_value=_mock_subprocess(ticket)), \
         patch("unseen_university.channel.post_to_channel") as mock_post:
        result = d._claim_next_ticket()

    assert result == ticket
    assert mock_post.called
    msg = mock_post.call_args[0][0]
    assert "DICKSIMNEL_WORKING" in msg
    assert "T-test" in msg
    assert mock_post.call_args[1]["author"] == "dicksimnel"


def test_claim_working_includes_title():
    d = _device()
    ticket = {"id": "T-abc", "title": "My important ticket", "status": "in_progress"}
    with patch("subprocess.run", return_value=_mock_subprocess(ticket)), \
         patch("unseen_university.channel.post_to_channel") as mock_post:
        d._claim_next_ticket()

    msg = mock_post.call_args[0][0]
    assert "My important ticket" in msg


def test_claim_channel_failure_still_returns_ticket():
    d = _device()
    ticket = {"id": "T-x", "title": "t", "status": "in_progress"}
    with patch("subprocess.run", return_value=_mock_subprocess(ticket)), \
         patch("unseen_university.channel.post_to_channel", side_effect=RuntimeError("down")):
        result = d._claim_next_ticket()

    assert result == ticket


def test_claim_no_ticket_no_channel_post():
    d = _device()
    mock_fail = MagicMock()
    mock_fail.returncode = 1
    mock_fail.stderr = "no ticket"
    with patch("subprocess.run", return_value=mock_fail), \
         patch("unseen_university.channel.post_to_channel") as mock_post:
        result = d._claim_next_ticket()

    assert result is None
    mock_post.assert_not_called()


# ── _post_result posts DONE ──────────────────────────────────────────────────


def test_post_result_posts_done():
    d = _device()
    d._run_queue_cmd = MagicMock(return_value=None)
    with patch("unseen_university.channel.post_to_channel") as mock_post:
        d._post_result("T-done", "DONE: fixed the bug nicely")

    assert mock_post.called
    msg = mock_post.call_args[0][0]
    assert "DICKSIMNEL_DONE" in msg
    assert "T-done" in msg
    assert mock_post.call_args[1]["author"] == "dicksimnel"


def test_post_result_done_includes_summary():
    d = _device()
    d._run_queue_cmd = MagicMock(return_value=None)
    with patch("unseen_university.channel.post_to_channel") as mock_post:
        d._post_result("T-x", "DONE: rewrote the parser and added tests")

    msg = mock_post.call_args[0][0]
    assert "rewrote the parser" in msg


def test_post_result_channel_failure_still_increments_counter():
    d = _device()
    d._run_queue_cmd = MagicMock(return_value=None)
    with patch("unseen_university.channel.post_to_channel", side_effect=RuntimeError("down")):
        d._post_result("T-x", "DONE: fixed")

    assert d._tickets_processed == 1


# ── _decline_ticket posts DECLINE ────────────────────────────────────────────


def test_decline_posts_decline():
    d = _device()
    d._run_queue_cmd = MagicMock(return_value=None)
    with patch("unseen_university.channel.post_to_channel") as mock_post:
        d._decline_ticket("T-nope", "inference proxy unavailable")

    assert mock_post.called
    msg = mock_post.call_args[0][0]
    assert "DICKSIMNEL_DECLINE" in msg
    assert "T-nope" in msg
    assert "inference proxy" in msg
    assert mock_post.call_args[1]["author"] == "dicksimnel"


def test_decline_channel_failure_still_resets_active_ticket():
    d = _device()
    d._active_ticket = "T-nope"
    d._run_queue_cmd = MagicMock(return_value=None)
    with patch("unseen_university.channel.post_to_channel", side_effect=RuntimeError("down")):
        d._decline_ticket("T-nope", "unavailable")

    assert d._active_ticket is None


# ── _escalate_ticket posts ESCALATE (via _channel_event) ────────────────────


def test_escalate_posts_escalate():
    d = _device()
    d._run_queue_cmd = MagicMock(return_value=None)
    with patch("unseen_university.channel.post_to_channel") as mock_post:
        d._escalate_ticket("T-hard", "HIGH-inertia tags: Security", analysis="complex analysis")

    assert mock_post.called
    msg = mock_post.call_args[0][0]
    assert "DICKSIMNEL_ESCALATE" in msg
    assert "T-hard" in msg
    assert "Security" in msg
    assert mock_post.call_args[1]["author"] == "dicksimnel"
