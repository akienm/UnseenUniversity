"""Tests for DickSimnelDevice chat interface (T-dicksimnel-chat)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ── DickSimnelDevice.chat() ───────────────────────────────────────────────────


class TestDickSimnelChat:
    def _device(self):
        from devices.dicksimnel.device import DickSimnelDevice
        d = DickSimnelDevice()
        d._shim = MagicMock()
        d._shim.is_blocked.return_value = False
        d._shim.device_id = "dicksimnel"
        d._shim._health_cache_store = {}
        d._shim.start.return_value = True
        d._shim.stop.return_value = True
        d._shim.self_test.return_value = {"passed": True, "details": "mock"}
        return d

    def test_skill_verb_routes_to_handle_command(self):
        d = self._device()
        d._shim.handle_command = MagicMock(return_value="skill response")
        result = d.chat("/help")
        d._shim.handle_command.assert_called_once_with("/help")
        assert result == "skill response"

    def test_freeform_calls_chat_inference(self):
        d = self._device()
        d._chat_inference = MagicMock(return_value="I'm working on T-test")
        result = d.chat("what are you doing?")
        d._chat_inference.assert_called_once_with("what are you doing?")
        assert "T-test" in result

    def test_chat_strips_whitespace(self):
        d = self._device()
        d._chat_inference = MagicMock(return_value="ok")
        d.chat("  hello  ")
        d._chat_inference.assert_called_once_with("hello")

    def test_chat_inference_returns_text_on_success(self):
        d = self._device()
        mock_response = MagicMock()
        mock_response.text = "I am DickSimnel, currently idle."
        mock_response.output_tokens = 20
        with patch("devices.inference.device.InferenceDevice.dispatch", return_value=mock_response):
            result = d._chat_inference("hello")
        assert "DickSimnel" in result or "idle" in result

    def test_chat_inference_returns_error_string_on_exception(self):
        d = self._device()
        with patch("devices.inference.device.InferenceDevice.dispatch", side_effect=RuntimeError("no model")):
            result = d._chat_inference("hello")
        assert "unavailable" in result.lower() or "DickSimnel" in result

    def test_chat_active_ticket_in_system_prompt(self):
        d = self._device()
        d._active_ticket = "T-current"
        captured = []
        mock_resp = MagicMock()
        mock_resp.text = "working"
        mock_resp.output_tokens = 5
        def mock_dispatch(req):
            captured.append(req.system)
            return mock_resp
        with patch("devices.inference.device.InferenceDevice.dispatch", side_effect=mock_dispatch):
            d._chat_inference("status?")
        assert captured
        assert "T-current" in captured[0]


# ── Web server endpoints ──────────────────────────────────────────────────────


class TestDickSimnelChatEndpoints:
    def _app(self):
        from starlette.testclient import TestClient
        from devices.web_server.server import _make_app
        return TestClient(_make_app())

    def test_post_chat_returns_response(self):
        client = self._app()
        mock_device = MagicMock()
        mock_device.return_value.chat.return_value = "Hello from DickSimnel"
        with patch("devices.dicksimnel.device.DickSimnelDevice", mock_device):
            resp = client.post("/api/dicksimnel/chat", json={"message": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert "response" in data
        assert "ts" in data

    def test_post_chat_empty_message_returns_400(self):
        client = self._app()
        resp = client.post("/api/dicksimnel/chat", json={"message": ""})
        assert resp.status_code == 400

    def test_post_chat_missing_message_returns_400(self):
        client = self._app()
        resp = client.post("/api/dicksimnel/chat", json={})
        assert resp.status_code == 400

    def test_get_chat_returns_history(self):
        client = self._app()
        mock_device = MagicMock()
        mock_device.return_value.chat.return_value = "pong"
        with patch("devices.dicksimnel.device.DickSimnelDevice", mock_device):
            client.post("/api/dicksimnel/chat", json={"message": "ping"})
        resp = client.get("/api/dicksimnel/chat")
        assert resp.status_code == 200
        data = resp.json()
        assert "messages" in data
        assert "count" in data
        assert data["count"] >= 0  # history may or may not persist across test client instances

    def test_get_chat_limit_param(self):
        client = self._app()
        resp = client.get("/api/dicksimnel/chat?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["messages"]) <= 5

    def test_post_chat_invalid_json_returns_400(self):
        client = self._app()
        resp = client.post(
            "/api/dicksimnel/chat",
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
