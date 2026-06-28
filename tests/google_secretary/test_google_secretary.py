"""Tests for GoogleSecretaryDevice, dispatcher, and shim."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── GoogleSecretaryShim ───────────────────────────────────────────────────────


class TestGoogleSecretaryShim:
    def _shim(self, tmp_path):
        from unseen_university.devices.google_secretary.shim import GoogleSecretaryShim
        return GoogleSecretaryShim(home=tmp_path)

    def test_start_creates_home(self, tmp_path):
        s = self._shim(tmp_path / "gs")
        s.start()
        assert (tmp_path / "gs").exists()

    def test_start_returns_true_without_credentials(self, tmp_path):
        s = self._shim(tmp_path)
        # start() must succeed even without credentials.json — degraded health, not failed start
        assert s.start() is True

    def test_self_test_fails_without_credentials(self, tmp_path):
        s = self._shim(tmp_path)
        result = s.self_test()
        assert not result["passed"]
        assert "credentials.json" in result["details"]

    def test_self_test_fails_without_valid_token(self, tmp_path):
        s = self._shim(tmp_path)
        # credentials.json present but no token
        (tmp_path / "credentials.json").write_text("{}")
        result = s.self_test()
        assert not result["passed"]

    def test_token_saved_with_owner_permissions(self, tmp_path):
        from unseen_university.devices.google_secretary.shim import GoogleSecretaryShim
        s = GoogleSecretaryShim(home=tmp_path)
        # Mock credentials object
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = '{"token": "abc"}'
        s._save_token(mock_creds)
        token_path = tmp_path / "token.json"
        assert token_path.exists()
        import stat
        mode = stat.filemode(token_path.stat().st_mode)
        assert mode == "-rw-------"  # 0o600

    def test_stop_clears_credentials(self, tmp_path):
        s = self._shim(tmp_path)
        s._creds = MagicMock()
        s.stop()
        assert s._creds is None

    def test_rollback_clears_state(self, tmp_path):
        s = self._shim(tmp_path)
        s._creds = MagicMock()
        s._started = True
        s.rollback()
        assert s._creds is None
        assert not s._started


# ── GoogleSecretaryDispatcher ─────────────────────────────────────────────────


class TestGoogleSecretaryDispatcher:
    def _dispatcher(self, tmp_path=None):
        from unseen_university.devices.google_secretary.dispatcher import GoogleSecretaryDispatcher
        mock_creds = MagicMock()
        mock_creds.valid = True
        return GoogleSecretaryDispatcher(
            home=tmp_path or Path("/tmp"),
            credentials_provider=lambda: mock_creds,
        )

    def test_unknown_action_returns_escalate(self):
        d = self._dispatcher()
        result = d.dispatch("unknown_action_xyz", {})
        assert result["status"] == "escalate"
        assert "unknown action" in result["error"]

    def test_calendar_create(self):
        d = self._dispatcher()
        mock_svc = MagicMock()
        mock_svc.events.return_value.insert.return_value.execute.return_value = {
            "id": "evt123", "htmlLink": "https://calendar.google.com/event?eid=123"
        }
        with patch.object(d, "_calendar", return_value=mock_svc):
            result = d.dispatch("calendar_create", {
                "summary": "Team standup",
                "start": "2026-06-10T09:00:00Z",
                "end": "2026-06-10T09:30:00Z",
            })
        assert result["status"] == "ok"
        assert result["result"]["id"] == "evt123"

    def test_calendar_list(self):
        d = self._dispatcher()
        mock_svc = MagicMock()
        mock_svc.events.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "e1", "summary": "Meeting"}]
        }
        with patch.object(d, "_calendar", return_value=mock_svc):
            result = d.dispatch("calendar_list", {})
        assert result["status"] == "ok"
        assert len(result["result"]) == 1

    def test_gmail_send(self):
        d = self._dispatcher()
        mock_svc = MagicMock()
        mock_svc.users.return_value.messages.return_value.send.return_value.execute.return_value = {
            "id": "msg456"
        }
        with patch.object(d, "_gmail", return_value=mock_svc):
            result = d.dispatch("gmail_send", {
                "to": "test@example.com",
                "subject": "Hello",
                "body": "Body text",
            })
        assert result["status"] == "ok"
        assert result["result"]["id"] == "msg456"

    def test_gmail_search(self):
        d = self._dispatcher()
        mock_svc = MagicMock()
        mock_svc.users.return_value.messages.return_value.list.return_value.execute.return_value = {
            "messages": [{"id": "m1"}, {"id": "m2"}]
        }
        with patch.object(d, "_gmail", return_value=mock_svc):
            result = d.dispatch("gmail_search", {"query": "from:someone@example.com"})
        assert result["status"] == "ok"
        assert len(result["result"]) == 2

    def test_tasks_create(self):
        d = self._dispatcher()
        mock_svc = MagicMock()
        mock_svc.tasks.return_value.insert.return_value.execute.return_value = {"id": "task789"}
        with patch.object(d, "_tasks", return_value=mock_svc):
            result = d.dispatch("tasks_create", {"title": "Buy groceries"})
        assert result["status"] == "ok"
        assert result["result"]["id"] == "task789"

    def test_tasks_list(self):
        d = self._dispatcher()
        mock_svc = MagicMock()
        mock_svc.tasks.return_value.list.return_value.execute.return_value = {
            "items": [{"id": "t1", "title": "Do laundry"}]
        }
        with patch.object(d, "_tasks", return_value=mock_svc):
            result = d.dispatch("tasks_list", {})
        assert result["status"] == "ok"

    def test_missing_credentials_returns_error(self):
        from unseen_university.devices.google_secretary.dispatcher import GoogleSecretaryDispatcher
        d = GoogleSecretaryDispatcher(credentials_provider=lambda: None)
        result = d.dispatch("calendar_list", {})
        assert result["status"] == "error"
        # Error is either "no credentials" or "package not installed" depending on env
        assert result["error"]

    def test_api_exception_returns_escalate(self):
        d = self._dispatcher()
        mock_svc = MagicMock()
        mock_svc.events.return_value.list.return_value.execute.side_effect = Exception("API 500")
        with patch.object(d, "_calendar", return_value=mock_svc):
            result = d.dispatch("calendar_list", {})
        assert result["status"] == "escalate"
        assert "API 500" in result["error"]


# ── GoogleSecretaryDevice ─────────────────────────────────────────────────────


class TestGoogleSecretaryDevice:
    def _device(self, tmp_path):
        from unseen_university.devices.google_secretary.device import GoogleSecretaryDevice
        d = GoogleSecretaryDevice(home=tmp_path)
        # Stub shim and dispatcher
        d._shim = MagicMock()
        d._shim.self_test.return_value = {"passed": True, "details": "mock"}
        d._dispatcher = MagicMock()
        return d

    def test_who_am_i(self, tmp_path):
        d = self._device(tmp_path)
        assert d.who_am_i()["device_id"] == "google_secretary"

    def test_health_healthy(self, tmp_path):
        d = self._device(tmp_path)
        assert d.health()["status"] == "healthy"

    def test_health_blocked(self, tmp_path):
        d = self._device(tmp_path)
        d.block("test")
        assert d.health()["status"] == "unhealthy"

    def test_handle_request_routes_to_dispatcher(self, tmp_path):
        d = self._device(tmp_path)
        d._dispatcher.dispatch.return_value = {"status": "ok", "result": {"id": "x"}}
        result = d.handle_request({
            "action": "calendar_list",
            "params": {},
            "request_id": "req1",
            "from_device": "granny",
        })
        d._dispatcher.dispatch.assert_called_once_with(action="calendar_list", params={})
        assert result["status"] == "ok"
        assert result["request_id"] == "req1"

    def test_restart_clears_state(self, tmp_path):
        d = self._device(tmp_path)
        d._blocked = True
        d._block_reason = "was blocked"
        d.restart()
        assert not d._blocked

    def test_recovery_clears_state(self, tmp_path):
        d = self._device(tmp_path)
        d._blocked = True
        d.recovery()
        assert not d._blocked
