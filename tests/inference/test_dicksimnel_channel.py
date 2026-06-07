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


# ── DickSimnelWorkerListener posts WORKING on dispatch ────────────────────────
# _claim_next_ticket was removed in T-dicksimnel-worker-listener — dispatch is
# now bus-push via DickSimnelWorkerListener._handle_dispatch(). Channel event
# coverage moves here.


class TestDickSimnelWorkerListenerChannelEvent:
    def _listener(self):
        from devices.dicksimnel.worker_listener import DickSimnelWorkerListener
        device = _device()
        bus = MagicMock()
        bus.fetch_unseen.return_value = []
        listener = DickSimnelWorkerListener(bus=bus, device=device)
        return listener, device

    def test_handle_dispatch_posts_working_event(self):
        listener, device = self._listener()
        ticket = {"id": "T-w", "title": "Build the widget", "tags": [], "description": "d"}
        device._fetch_ticket = MagicMock(return_value=ticket)
        device._should_escalate = MagicMock(return_value=(False, ""))
        device._run_inference = MagicMock(return_value="DONE: built it")
        device._post_result = MagicMock()

        with patch("unseen_university.channel.post_to_channel") as mock_post:
            listener._handle_dispatch("T-w", "granny.0")

        events = [c[0][0] for c in mock_post.call_args_list]
        assert any("DICKSIMNEL_WORKING" in e and "T-w" in e for e in events), \
            f"expected DICKSIMNEL_WORKING event, got: {events}"

    def test_handle_dispatch_working_event_includes_title(self):
        listener, device = self._listener()
        ticket = {"id": "T-t", "title": "My ticket title", "tags": [], "description": "d"}
        device._fetch_ticket = MagicMock(return_value=ticket)
        device._should_escalate = MagicMock(return_value=(False, ""))
        device._run_inference = MagicMock(return_value="DONE: done")
        device._post_result = MagicMock()

        with patch("unseen_university.channel.post_to_channel") as mock_post:
            listener._handle_dispatch("T-t", "granny.0")

        events = [c[0][0] for c in mock_post.call_args_list]
        assert any("My ticket title" in e for e in events), \
            f"title missing from channel events: {events}"


# ── _post_result posts DONE ──────────────────────────────────────────────────


def test_post_result_posts_done():
    d = _device()
    d._run_queue_cmd = MagicMock(return_value={"status": "closed"})
    with patch("unseen_university.channel.post_to_channel") as mock_post:
        d._post_result("T-done", "DONE: fixed the bug nicely")

    assert mock_post.called
    msg = mock_post.call_args[0][0]
    assert "DICKSIMNEL_DONE" in msg
    assert "T-done" in msg
    assert mock_post.call_args[1]["author"] == "dicksimnel"


def test_post_result_done_includes_summary():
    d = _device()
    d._run_queue_cmd = MagicMock(return_value={"status": "closed"})
    with patch("unseen_university.channel.post_to_channel") as mock_post:
        d._post_result("T-x", "DONE: rewrote the parser and added tests")

    msg = mock_post.call_args[0][0]
    assert "rewrote the parser" in msg


def test_post_result_channel_failure_still_increments_counter():
    d = _device()
    d._run_queue_cmd = MagicMock(return_value={"status": "closed"})
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
