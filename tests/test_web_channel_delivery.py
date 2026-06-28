"""
test_web_channel_delivery.py — T-swarm-web-tab-ui: channel tab → tmux delivery.

The fallback HTML already has channel tabs and WebSocket streaming.
These tests verify the new tmux attribution path: when an agent registers
with a tmux_target, messages sent on that agent's channel are forwarded
via send-keys with attribution.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import unseen_university.devices.web_server.server as _srv


@pytest.fixture(autouse=True)
def _reset_server_state():
    """Clear module-level state between tests."""
    _srv._agents.clear()
    _srv._agent_stats.clear()
    _srv._session_clients.clear()
    _srv._session_history.clear()
    _srv._client_session.clear()
    _srv._loop = None
    yield
    _srv._agents.clear()
    _srv._agent_stats.clear()
    _srv._session_clients.clear()
    _srv._session_history.clear()
    _srv._client_session.clear()
    _srv._loop = None


def _fake_agent(tmux_target: str = "claude-main") -> dict:
    return {
        "registered_at": "2026-01-01T00:00:00",
        "capabilities": [],
        "callback_url": "",
        "tmux_target": tmux_target,
        "last_heartbeat": 0.0,
    }


# ── _deliver_to_tmux unit tests ───────────────────────────────────────────────


class TestDeliverToTmux:
    def test_delivers_to_named_agent_channel(self):
        _srv._agents["igor"] = _fake_agent("claude-main")
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            _srv._deliver_to_tmux("hello", "akien", "comms://igor")

        mock_run.assert_called_once()
        cmd = mock_run.call_args.args[0]
        assert "send-keys" in cmd
        assert "claude-main" in cmd
        assert "akien: hello" in cmd
        assert "Enter" in cmd

    def test_shared_channel_delivers_to_all_agents_with_target(self):
        _srv._agents["igor"] = _fake_agent("claude-main")
        _srv._agents["cc"] = _fake_agent("cc-session")
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            _srv._deliver_to_tmux("ping", "akien", "comms://shared")

        assert mock_run.call_count == 2
        targets = {
            call.args[0][call.args[0].index("-t") + 1]
            for call in mock_run.call_args_list
        }
        assert targets == {"claude-main", "cc-session"}

    def test_no_delivery_when_tmux_target_empty(self):
        _srv._agents["igor"] = _fake_agent("")
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            _srv._deliver_to_tmux("hello", "akien", "comms://shared")

        mock_run.assert_not_called()

    def test_no_delivery_when_no_agents_registered(self):
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            _srv._deliver_to_tmux("hello", "akien", "comms://shared")

        mock_run.assert_not_called()

    def test_unregistered_agent_channel_no_delivery(self):
        _srv._agents["igor"] = _fake_agent("claude-main")
        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            _srv._deliver_to_tmux("hello", "akien", "comms://notes")

        mock_run.assert_not_called()

    def test_tmux_failure_does_not_raise(self):
        _srv._agents["igor"] = _fake_agent("claude-main")
        with patch("subprocess.run", side_effect=OSError("tmux not found")):
            _srv._deliver_to_tmux("hello", "akien", "comms://igor")


# ── Agent registration with tmux_target ──────────────────────────────────────


class TestAgentRegistrationTmuxTarget:
    def test_register_stores_tmux_target(self):
        from starlette.testclient import TestClient

        with patch("unseen_university.devices.web_server.server._init_comms"):
            app = _srv._make_app()
        with TestClient(app) as client:
            resp = client.post(
                "/api/agents/register",
                json={"agent_id": "igor", "tmux_target": "claude-main"},
            )
        assert resp.status_code == 200
        assert _srv._agents["igor"]["tmux_target"] == "claude-main"

    def test_register_without_tmux_target_defaults_empty(self):
        from starlette.testclient import TestClient

        with patch("unseen_university.devices.web_server.server._init_comms"):
            app = _srv._make_app()
        with TestClient(app) as client:
            resp = client.post(
                "/api/agents/register",
                json={"agent_id": "igor"},
            )
        assert resp.status_code == 200
        assert _srv._agents["igor"]["tmux_target"] == ""

    def test_tmux_target_capped_at_128_chars(self):
        from starlette.testclient import TestClient

        with patch("unseen_university.devices.web_server.server._init_comms"):
            app = _srv._make_app()
        long_target = "x" * 200
        with TestClient(app) as client:
            client.post(
                "/api/agents/register",
                json={"agent_id": "igor", "tmux_target": long_target},
            )
        assert len(_srv._agents["igor"]["tmux_target"]) == 128


# ── WebSocket → tmux smoke test ───────────────────────────────────────────────


class TestWebSocketTmuxDelivery:
    def test_ws_message_on_agent_channel_triggers_send_keys(self):
        """Smoke test: WS message on comms://igor → tmux send-keys to claude-main."""
        from starlette.testclient import TestClient

        _srv._agents["igor"] = _fake_agent("claude-main")

        mock_result = MagicMock(returncode=0, stdout="", stderr="")
        with (
            patch("unseen_university.devices.web_server.server._init_comms"),
            patch("unseen_university.devices.web_server.server._load_agents"),
            patch("subprocess.run", return_value=mock_result) as mock_run,
        ):
            app = _srv._make_app()
            with TestClient(app) as client:
                with client.websocket_connect("/ws") as ws:
                    ws.send_json({"type": "join_session", "session_id": "comms://igor"})
                    ws.send_json(
                        {
                            "type": "message",
                            "author": "akien",
                            "content": "status update please",
                        }
                    )

        calls = [
            c
            for c in mock_run.call_args_list
            if "send-keys" in (c.args[0] if c.args else [])
        ]
        assert calls, "expected at least one tmux send-keys call"
        cmd = calls[0].args[0]
        assert "claude-main" in cmd
        assert "akien: status update please" in cmd
