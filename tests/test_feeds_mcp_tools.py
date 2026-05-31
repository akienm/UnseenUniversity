"""Tests for feeds layer: fetch_recent, send_to, send_feed, view_feed, Granny publish."""

from __future__ import annotations

import os

# Must be set before any bus imports so _TEST_MODE=True at module load.
os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

import json
from unittest.mock import MagicMock, patch

import pytest

from bus.envelope import Envelope
from bus.imap_server import IMAPServer, _STUB_MAILBOXES, _STUB_SEEN

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_stub():
    """Clear stub state before each test — avoids bleed between tests."""
    _STUB_MAILBOXES.clear()
    _STUB_SEEN.clear()
    yield
    _STUB_MAILBOXES.clear()
    _STUB_SEEN.clear()


@pytest.fixture()
def imap():
    """Unstarted IMAPServer in test mode — works directly against stub globals."""
    return IMAPServer()


# ── IMAPServer.fetch_recent ────────────────────────────────────────────────────


class TestFetchRecent:
    def test_returns_empty_for_missing_mailbox(self, imap):
        result = imap.fetch_recent("feeds/nonexistent", 20)
        assert result == []

    def test_returns_all_when_fewer_than_limit(self, imap):
        imap.create_mailbox("feeds/granny")
        for i in range(3):
            imap.append(
                "feeds/granny", Envelope.now("Granny.0", "feeds/granny", {"i": i})
            )
        result = imap.fetch_recent("feeds/granny", 20)
        assert len(result) == 3

    def test_returns_last_n_when_over_limit(self, imap):
        imap.create_mailbox("feeds/granny")
        for i in range(5):
            imap.append(
                "feeds/granny", Envelope.now("Granny.0", "feeds/granny", {"i": i})
            )
        result = imap.fetch_recent("feeds/granny", 3)
        assert len(result) == 3
        assert result[0].payload["i"] == 2
        assert result[2].payload["i"] == 4

    def test_does_not_mark_seen(self, imap):
        imap.create_mailbox("feeds/granny")
        imap.append("feeds/granny", Envelope.now("A", "feeds/granny", {}))
        imap.fetch_recent("feeds/granny", 10)
        # unseen count unchanged — fetch_recent is non-destructive
        assert imap.unseen_count("feeds/granny") == 1

    def test_returns_envelopes_not_bytes(self, imap):
        imap.create_mailbox("feeds/test")
        imap.append("feeds/test", Envelope.now("src", "feeds/test", {"x": 1}))
        result = imap.fetch_recent("feeds/test", 5)
        assert isinstance(result[0], Envelope)
        assert result[0].payload["x"] == 1


# ── MCP tools: send_to, send_feed, view_feed ──────────────────────────────────


def _call_tool(name: str, args: dict) -> dict:
    """Drive the mcp_server dispatch function with a tools/call message."""
    # Reset the module-level singleton so each test gets a fresh unstarted client
    import devices.queue.mcp_server as ms

    ms._feeds_imap = None
    msg = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }
    response = ms._dispatch(msg)
    assert response is not None
    text = response["result"]["content"][0]["text"]
    return json.loads(text)


class TestSendToTool:
    def test_creates_mailbox_and_appends(self):
        result = _call_tool("send_to", {"receiver": "granny", "message": "hello"})
        assert result["status"] == "ok"
        assert result["mailbox"] == "feeds/granny"
        assert "feeds/granny" in _STUB_MAILBOXES
        assert len(_STUB_MAILBOXES["feeds/granny"]) == 1

    def test_message_in_payload(self):
        _call_tool("send_to", {"receiver": "granny", "message": "dispatch T-foo"})
        raw = _STUB_MAILBOXES["feeds/granny"][0]
        env = Envelope.from_json(raw.decode())
        assert env.payload["message"] == "dispatch T-foo"
        assert env.payload["kind"] == "send_to"

    def test_multiple_sends_accumulate(self):
        _call_tool("send_to", {"receiver": "granny", "message": "a"})
        _call_tool("send_to", {"receiver": "granny", "message": "b"})
        assert len(_STUB_MAILBOXES["feeds/granny"]) == 2


class TestSendFeedTool:
    def test_publishes_to_sender_mailbox(self):
        result = _call_tool(
            "send_feed", {"event": "T-abc dispatched", "sender": "granny"}
        )
        assert result["status"] == "ok"
        assert result["mailbox"] == "feeds/granny"
        assert len(_STUB_MAILBOXES["feeds/granny"]) == 1

    def test_defaults_sender_to_cc(self):
        result = _call_tool("send_feed", {"event": "starting sprint"})
        assert result["mailbox"] == "feeds/cc"

    def test_event_in_payload(self):
        _call_tool("send_feed", {"event": "audit_fail T-xyz", "sender": "granny"})
        raw = _STUB_MAILBOXES["feeds/granny"][0]
        env = Envelope.from_json(raw.decode())
        assert env.payload["event"] == "audit_fail T-xyz"
        assert env.payload["kind"] == "send_feed"


class TestViewFeedTool:
    def test_empty_mailbox_returns_empty(self):
        _STUB_MAILBOXES["feeds/granny"] = []
        result = _call_tool("view_feed", {"sender": "granny"})
        assert result["events"] == []
        assert result["count"] == 0

    def test_missing_mailbox_returns_empty(self):
        result = _call_tool("view_feed", {"sender": "nonexistent"})
        assert result["events"] == []

    def test_returns_events_in_order(self):
        imap = IMAPServer()
        imap.create_mailbox("feeds/granny")
        for i in range(3):
            imap.append(
                "feeds/granny", Envelope.now("Granny.0", "feeds/granny", {"i": i})
            )
        result = _call_tool("view_feed", {"sender": "granny"})
        assert result["count"] == 3
        payloads = [e["payload"]["i"] for e in result["events"]]
        assert payloads == [0, 1, 2]

    def test_limit_respected(self):
        imap = IMAPServer()
        imap.create_mailbox("feeds/granny")
        for i in range(5):
            imap.append(
                "feeds/granny", Envelope.now("Granny.0", "feeds/granny", {"i": i})
            )
        result = _call_tool("view_feed", {"sender": "granny", "limit": 2})
        assert result["count"] == 2
        payloads = [e["payload"]["i"] for e in result["events"]]
        assert payloads == [3, 4]

    def test_non_destructive(self):
        imap = IMAPServer()
        imap.create_mailbox("feeds/granny")
        imap.append("feeds/granny", Envelope.now("Granny.0", "feeds/granny", {}))
        _call_tool("view_feed", {"sender": "granny"})
        # second call still returns the event
        result = _call_tool("view_feed", {"sender": "granny"})
        assert result["count"] == 1


# ── GrannyDaemon feed publishing ──────────────────────────────────────────────


def _make_bare_daemon(audit_passed=True, route_ok=True):
    """Bypass __init__ and wire a mock IMAP."""
    from devices.granny.daemon import GrannyDaemon

    daemon = GrannyDaemon.__new__(GrannyDaemon)
    daemon._dispatched_ids = set()
    daemon._alerted_ids = set()
    daemon._total_dispatched = 0
    daemon._total_errors = 0
    daemon._last_poll = None
    daemon._imap = MagicMock()

    audit = MagicMock()
    audit.passed = audit_passed
    audit.escalate_to_cc = True
    audit.reasons = []

    device = MagicMock()
    device.intake_ticket.return_value = audit
    device.route_ticket.return_value = (route_ok, "cc")
    daemon._device = device
    return daemon


def _ticket(id="T-abc", status="sprint", worker="claude", tags=None):
    return {
        "id": id,
        "title": f"ticket {id}",
        "size": "S",
        "status": status,
        "tags": tags or ["Platform"],
        "worker": worker,
    }


class TestGrannyDaemonFeedPublish:
    def test_dispatch_publishes_feed_event(self):
        daemon = _make_bare_daemon(audit_passed=True, route_ok=True)
        tickets = [_ticket("T-ok")]
        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(True, None, 0.0),
            ),
            patch("devices.granny.daemon._count_active_cc_sessions", return_value=0),
        ):
            daemon.run_once()
        calls = daemon._imap.append.call_args_list
        feed_calls = [c for c in calls if c[0][0] == "feeds/granny"]
        assert len(feed_calls) == 1
        env = feed_calls[0][0][1]
        assert env.payload["kind"] == "dispatch"
        assert env.payload["ticket_id"] == "T-ok"

    def test_audit_fail_publishes_feed_event(self):
        daemon = _make_bare_daemon()
        daemon._device.intake_ticket.return_value = MagicMock(
            passed=False, escalate_to_cc=False, reasons=["missing section"]
        )
        tickets = [_ticket("T-bad")]
        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(True, None, 0.0),
            ),
            patch("devices.granny.daemon._count_active_cc_sessions", return_value=0),
        ):
            daemon.run_once()
        calls = daemon._imap.append.call_args_list
        feed_calls = [c for c in calls if c[0][0] == "feeds/granny"]
        assert len(feed_calls) == 1
        env = feed_calls[0][0][1]
        assert env.payload["kind"] == "audit_fail"
        assert env.payload["ticket_id"] == "T-bad"

    def test_route_fail_publishes_feed_event(self):
        daemon = _make_bare_daemon()
        daemon._device.intake_ticket.return_value = MagicMock(
            passed=True, escalate_to_cc=False, reasons=[]
        )
        daemon._device.route_ticket.return_value = (False, "cc")
        tickets = [_ticket("T-noway")]
        with (
            patch("devices.granny.daemon._load_sprint_tickets", return_value=tickets),
            patch("devices.granny.daemon._ticket_needs_cc", return_value=True),
            patch(
                "devices.granny.daemon._check_rate_limit",
                return_value=(True, None, 0.0),
            ),
            patch("devices.granny.daemon._count_active_cc_sessions", return_value=0),
        ):
            daemon.run_once()
        calls = daemon._imap.append.call_args_list
        feed_calls = [c for c in calls if c[0][0] == "feeds/granny"]
        assert len(feed_calls) == 1
        env = feed_calls[0][0][1]
        assert env.payload["kind"] == "route_fail"
        assert env.payload["ticket_id"] == "T-noway"

    def test_publish_feed_skipped_when_imap_none(self):
        daemon = _make_bare_daemon()
        daemon._imap = None
        daemon._publish_feed("dispatch", "T-x", "details")  # must not raise
