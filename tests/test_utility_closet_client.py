"""Tests for utility_closet_client.py — D335 Phase 2.

Tests the Igor-side client that registers with the utility closet platform.
Uses mock HTTP responses to avoid requiring a running server.
"""

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _make_client():
    """Create a fresh UtilityClosetClient instance."""
    from wild_igor.igor.web.utility_closet_client import UtilityClosetClient

    return UtilityClosetClient()


def _mock_urlopen(response_data, status=200):
    """Create a mock for urllib.request.urlopen that returns JSON data."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(response_data).encode("utf-8")
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestIsAvailable:
    """Test health check."""

    @patch("wild_igor.igor.web.utility_closet_client.urllib.request.urlopen")
    def test_available_when_healthy(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen({"status": "ok"})
        client = _make_client()
        assert client.is_available()

    @patch("wild_igor.igor.web.utility_closet_client.urllib.request.urlopen")
    def test_unavailable_on_connection_error(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        client = _make_client()
        assert not client.is_available()

    @patch("wild_igor.igor.web.utility_closet_client.urllib.request.urlopen")
    def test_unavailable_on_bad_status(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen({"status": "degraded"})
        client = _make_client()
        assert not client.is_available()


class TestRegister:
    """Test agent registration."""

    @patch("wild_igor.igor.web.utility_closet_client.urllib.request.urlopen")
    def test_register_success(self, mock_urlopen):
        # First call: health check. Second call: register.
        mock_urlopen.side_effect = [
            _mock_urlopen({"status": "ok"}),
            _mock_urlopen({"status": "ok", "agent_id": "igor"}),
        ]
        client = _make_client()
        assert client.register("igor", capabilities=["chat"])
        assert client.is_registered
        assert client.agent_id == "igor"

    @patch("wild_igor.igor.web.utility_closet_client.urllib.request.urlopen")
    def test_register_fails_when_unavailable(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")
        client = _make_client()
        assert not client.register("igor")
        assert not client.is_registered

    @patch("wild_igor.igor.web.utility_closet_client.urllib.request.urlopen")
    def test_register_fails_on_error_response(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_urlopen({"status": "ok"}),  # health check
            _mock_urlopen({"error": "duplicate agent"}),  # register
        ]
        client = _make_client()
        assert not client.register("igor")
        assert not client.is_registered


class TestDeregister:
    """Test agent deregistration."""

    @patch("wild_igor.igor.web.utility_closet_client.urllib.request.urlopen")
    def test_deregister_success(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_urlopen({"status": "ok"}),  # health
            _mock_urlopen({"status": "ok", "agent_id": "igor"}),  # register
            _mock_urlopen({"status": "ok"}),  # deregister
        ]
        client = _make_client()
        client.register("igor")
        assert client.deregister()
        assert not client.is_registered

    def test_deregister_noop_when_not_registered(self):
        client = _make_client()
        assert client.deregister()  # Should return True (nothing to do)

    @patch("wild_igor.igor.web.utility_closet_client.urllib.request.urlopen")
    def test_deregister_handles_failure(self, mock_urlopen):
        import urllib.error

        mock_urlopen.side_effect = [
            _mock_urlopen({"status": "ok"}),  # health
            _mock_urlopen({"status": "ok", "agent_id": "igor"}),  # register
            urllib.error.URLError("Connection refused"),  # deregister fails
        ]
        client = _make_client()
        client.register("igor")
        assert not client.deregister()  # Returns False but doesn't raise
        assert not client.is_registered  # Still marks as unregistered


class TestPushStats:
    """Test stats push."""

    @patch("wild_igor.igor.web.utility_closet_client.urllib.request.urlopen")
    def test_push_stats_success(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_urlopen({"status": "ok"}),  # health
            _mock_urlopen({"status": "ok", "agent_id": "igor"}),  # register
            _mock_urlopen({"status": "ok"}),  # stats push
        ]
        client = _make_client()
        client.register("igor")
        assert client.push_stats({"memory_count": 42, "session_cost": 0.05})

    def test_push_stats_noop_when_not_registered(self):
        client = _make_client()
        assert not client.push_stats({"memory_count": 42})


class TestSendMessage:
    """Test message sending."""

    @patch("wild_igor.igor.web.utility_closet_client.urllib.request.urlopen")
    def test_send_message_success(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_urlopen({"status": "ok"}),  # health
            _mock_urlopen({"status": "ok", "agent_id": "igor"}),  # register
            _mock_urlopen({"status": "ok"}),  # send
        ]
        client = _make_client()
        client.register("igor")
        assert client.send_message("hello world", session_id="shared")

    def test_send_message_noop_when_not_registered(self):
        client = _make_client()
        assert not client.send_message("hello")


class TestPollMessages:
    """Test message polling."""

    @patch("wild_igor.igor.web.utility_closet_client.urllib.request.urlopen")
    def test_poll_returns_messages(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_urlopen({"status": "ok"}),  # health
            _mock_urlopen({"status": "ok", "agent_id": "igor"}),  # register
            _mock_urlopen(
                {
                    "messages": [
                        {"content": "hi", "author": "web-user"},
                    ]
                }
            ),  # poll
        ]
        client = _make_client()
        client.register("igor")
        msgs = client.poll_messages()
        assert len(msgs) == 1
        assert msgs[0]["content"] == "hi"

    @patch("wild_igor.igor.web.utility_closet_client.urllib.request.urlopen")
    def test_poll_returns_empty_on_no_messages(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_urlopen({"status": "ok"}),  # health
            _mock_urlopen({"status": "ok", "agent_id": "igor"}),  # register
            _mock_urlopen({"messages": []}),  # poll
        ]
        client = _make_client()
        client.register("igor")
        assert client.poll_messages() == []

    def test_poll_returns_empty_when_not_registered(self):
        client = _make_client()
        assert client.poll_messages() == []


class TestStatsPusher:
    """Test background stats pushing thread."""

    @patch("wild_igor.igor.web.utility_closet_client.urllib.request.urlopen")
    def test_stats_pusher_starts_when_registered(self, mock_urlopen):
        mock_urlopen.side_effect = [
            _mock_urlopen({"status": "ok"}),  # health
            _mock_urlopen({"status": "ok", "agent_id": "igor"}),  # register
        ] + [
            _mock_urlopen({"status": "ok"})
        ] * 10  # stats pushes

        client = _make_client()
        client.register("igor")
        client.start_stats_pusher(lambda: {"memory_count": 42}, interval=0.1)

        import time

        time.sleep(0.3)
        client._stop_event.set()  # Stop the thread

        assert client._stats_thread is not None
        assert client._stats_thread.daemon

    def test_stats_pusher_noop_when_not_registered(self):
        client = _make_client()
        client.start_stats_pusher(lambda: {})
        assert client._stats_thread is None
