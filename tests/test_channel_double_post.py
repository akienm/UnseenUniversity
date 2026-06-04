"""Tests for T-channel-double-post fix — ws_only=1 prevents duplicate Postgres writes."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch


def _make_app():
    import devices.web_server.server as _srv
    with patch("devices.web_server.server._init_comms"):
        return _srv._make_app()


class TestWsOnlyParam:
    def test_ws_only_skips_channel_append(self):
        """POST /api/agents/{id}/send?ws_only=1 must NOT call _channel_append."""
        from starlette.testclient import TestClient
        app = _make_app()
        with patch("devices.web_server.server._channel_append") as mock_append:
            with patch("devices.web_server.server._broadcast_to_session"):
                with TestClient(app) as client:
                    resp = client.post(
                        "/api/agents/scraps/send?ws_only=1",
                        json={"content": "hello", "session_id": "shared"},
                    )
        assert resp.status_code == 200
        mock_append.assert_not_called()

    def test_without_ws_only_calls_channel_append(self):
        """POST without ws_only=1 must still call _channel_append (direct-caller compat)."""
        from starlette.testclient import TestClient
        app = _make_app()
        with patch("devices.web_server.server._channel_append") as mock_append:
            with patch("devices.web_server.server._broadcast_to_session"):
                with TestClient(app) as client:
                    resp = client.post(
                        "/api/agents/scraps/send",
                        json={"content": "hello", "session_id": "shared"},
                    )
        assert resp.status_code == 200
        mock_append.assert_called_once()

    def test_agent_send_persist_false_skips_channel_append(self):
        """agent_send(persist=False) must not call _channel_append."""
        import devices.web_server.server as srv
        with patch("devices.web_server.server._channel_append") as mock_append:
            with patch("devices.web_server.server._broadcast_to_session"):
                srv.agent_send("msg", "scraps", "shared", persist=False)
        mock_append.assert_not_called()

    def test_agent_send_persist_true_calls_channel_append(self):
        """agent_send(persist=True) must call _channel_append (default behavior)."""
        import devices.web_server.server as srv
        with patch("devices.web_server.server._channel_append") as mock_append:
            with patch("devices.web_server.server._broadcast_to_session"):
                srv.agent_send("msg", "scraps", "shared", persist=True)
        mock_append.assert_called_once()


class TestWsPushChannelPyParam:
    def test_ws_push_includes_ws_only_param(self):
        """_ws_push must include ?ws_only=1 so agent_send skips _channel_append."""
        from unseen_university.channel import _ws_push
        import urllib.request as _req

        opened_urls = []

        def fake_urlopen(req, timeout=None):
            opened_urls.append(req.full_url)
            return MagicMock().__enter__.return_value

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            try:
                _ws_push("hello", "scraps", "shared")
            except Exception:
                pass

        assert opened_urls, "urlopen was not called"
        assert "ws_only=1" in opened_urls[0], f"ws_only=1 missing from URL: {opened_urls[0]}"
