"""tests/test_uc_delivery_telemetry.py — UC server delivery telemetry.

Tests the logging and channel-diagnostic points added by T-uc-delivery-telemetry
in lab/claudecode/utility_closet_server.py:

  1. _api_agent_send logs POST acceptance / rejections
  2. agent_send logs entry
  3. _broadcast_to_session logs fanout count (non-zero case)
  4. _broadcast_to_session logs DROP + posts channel diagnostic (zero case)
  5. _broadcast_to_session handles missing event loop gracefully

Tests operate on the module's functions directly with monkeypatched globals
(event loop, session clients, channel_append). No Starlette test client
needed — delivery logic is synchronous enough to exercise inline.
"""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(scope="module")
def ucs():
    """Import the UC server module once per test module."""
    from lab.claudecode import utility_closet_server as _ucs

    return _ucs


@pytest.fixture
def clean_state(ucs):
    """Reset module-level state between tests. Restore on teardown."""
    original_clients = dict(ucs._session_clients)
    ucs._session_clients.clear()
    yield
    ucs._session_clients.clear()
    ucs._session_clients.update(original_clients)


# ── _broadcast_to_session ────────────────────────────────────────────────────


class TestBroadcastToSession:
    def test_zero_fanout_logs_drop_warning(self, ucs, clean_state, caplog):
        """When no clients are subscribed to session_id, log DROP + diag."""
        ucs._loop = MagicMock()
        payload = json.dumps({"content": "hello"})

        with patch.object(ucs, "_channel_append") as mock_ca:
            with caplog.at_level(logging.WARNING, logger="utility_closet"):
                ucs._broadcast_to_session("missing_session", payload)

        assert any("fanout=0" in r.message for r in caplog.records)
        assert any("DROP" in r.message for r in caplog.records)
        # Channel diagnostic posted
        assert mock_ca.called
        args, kwargs = mock_ca.call_args
        assert args[0] == "uc_deliver"
        assert "session=missing_session" in args[1]
        assert "fanout=0" in args[1]

    def test_zero_fanout_diagnostic_includes_preview(self, ucs, clean_state):
        ucs._loop = MagicMock()
        payload = json.dumps({"content": "this is the actual body"})

        with patch.object(ucs, "_channel_append") as mock_ca:
            ucs._broadcast_to_session("missing_session", payload)

        diag = mock_ca.call_args[0][1]
        assert "this is the actual body" in diag

    def test_nonzero_fanout_logs_info_no_diagnostic(self, ucs, clean_state, caplog):
        mock_loop = MagicMock()
        ucs._loop = mock_loop

        q1 = MagicMock()
        q2 = MagicMock()
        ucs._session_clients["shared"] = [q1, q2]

        payload = json.dumps({"content": "hi"})
        with patch.object(ucs, "_channel_append") as mock_ca:
            with caplog.at_level(logging.INFO, logger="utility_closet"):
                ucs._broadcast_to_session("shared", payload)

        # Fanout count logged at INFO
        assert any("fanout=2" in r.message for r in caplog.records)
        # No diagnostic posted (only fires on zero fanout)
        assert not mock_ca.called
        # Each queue got an enqueue call
        assert mock_loop.call_soon_threadsafe.call_count == 2

    def test_no_event_loop_logs_warning_not_raises(self, ucs, clean_state, caplog):
        ucs._loop = None
        with caplog.at_level(logging.WARNING, logger="utility_closet"):
            ucs._broadcast_to_session("any", "{}")
        assert any("no event loop" in r.message for r in caplog.records)

    def test_enqueue_exception_logged_not_raised(self, ucs, clean_state, caplog):
        """If call_soon_threadsafe raises, it's logged per-queue and continues."""
        mock_loop = MagicMock()
        mock_loop.call_soon_threadsafe.side_effect = RuntimeError("loop closed")
        ucs._loop = mock_loop

        q1 = MagicMock()
        q2 = MagicMock()
        ucs._session_clients["s"] = [q1, q2]

        with caplog.at_level(logging.WARNING, logger="utility_closet"):
            ucs._broadcast_to_session("s", "{}")

        assert sum(1 for r in caplog.records if "enqueue failed" in r.message) == 2

    def test_non_json_payload_preview_still_logs(self, ucs, clean_state, caplog):
        """Payload preview fallback when payload isn't JSON."""
        ucs._loop = MagicMock()
        with patch.object(ucs, "_channel_append"):
            with caplog.at_level(logging.WARNING, logger="utility_closet"):
                ucs._broadcast_to_session("missing", "plain text not json")
        assert any("plain text not json" in r.message for r in caplog.records)


# ── agent_send ───────────────────────────────────────────────────────────────


class TestAgentSend:
    def test_agent_send_logs_entry(self, ucs, clean_state, caplog):
        ucs._loop = MagicMock()
        with patch.object(ucs, "_channel_append"):
            with caplog.at_level(logging.INFO, logger="utility_closet"):
                ucs.agent_send("hello world", "igor", session_id="shared")

        entry_logs = [r for r in caplog.records if "agent_send agent=igor" in r.message]
        assert len(entry_logs) == 1
        assert "session=shared" in entry_logs[0].message
        assert "len=11" in entry_logs[0].message

    def test_agent_send_to_unknown_session_produces_drop(
        self, ucs, clean_state, caplog
    ):
        """agent_send → _broadcast_to_session → zero fanout → DROP logged."""
        ucs._loop = MagicMock()
        with patch.object(ucs, "_channel_append") as mock_ca:
            with caplog.at_level(logging.WARNING, logger="utility_closet"):
                ucs.agent_send("payload", "igor", session_id="ghost_session")

        assert any("DROP session=ghost_session" in r.message for r in caplog.records)
        # channel diag fires (agent_send also calls _channel_append for the
        # normal channel mirror; we check at least one call has the uc_deliver
        # diagnostic shape).
        diag_calls = [
            c
            for c in mock_ca.call_args_list
            if len(c[0]) >= 2 and "[uc_deliver]" in c[0][1]
        ]
        assert len(diag_calls) == 1


# ── _api_agent_send (POST entry) ─────────────────────────────────────────────


class TestApiAgentSend:
    def test_accepted_post_logs_info(self, ucs, clean_state, caplog):
        """Drive _api_agent_send with a fake Starlette request."""
        ucs._loop = MagicMock()

        class FakeRequest:
            path_params = {"agent_id": "igor"}

            async def json(self):
                return {"content": "a real reply", "session_id": "shared"}

        with patch.object(ucs, "agent_send") as mock_send, patch.object(
            ucs, "_channel_append"
        ):
            with caplog.at_level(logging.INFO, logger="utility_closet"):
                resp = asyncio.get_event_loop().run_until_complete(
                    ucs._api_agent_send(FakeRequest())
                )

        assert mock_send.called
        assert any(
            "POST accepted agent=igor session=shared" in r.message
            for r in caplog.records
        )

    def test_empty_content_rejected_with_log(self, ucs, clean_state, caplog):
        class FakeRequest:
            path_params = {"agent_id": "igor"}

            async def json(self):
                return {"content": "   ", "session_id": "shared"}

        with caplog.at_level(logging.WARNING, logger="utility_closet"):
            resp = asyncio.get_event_loop().run_until_complete(
                ucs._api_agent_send(FakeRequest())
            )

        assert any("empty content" in r.message for r in caplog.records)

    def test_invalid_json_rejected_with_log(self, ucs, clean_state, caplog):
        class FakeRequest:
            path_params = {"agent_id": "igor"}

            async def json(self):
                raise ValueError("bad json")

        with caplog.at_level(logging.WARNING, logger="utility_closet"):
            resp = asyncio.get_event_loop().run_until_complete(
                ucs._api_agent_send(FakeRequest())
            )

        assert any("invalid JSON" in r.message for r in caplog.records)
