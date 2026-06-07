"""Tests for NotificationDispatcher and BaseShim.filter_incoming integration."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from unseen_university.notification_dispatcher import NotificationDispatcher, _PENDING_FILENAME
from unseen_university.notify import DeliveryMode, NotificationConfig
from unseen_university.shim import BaseShim


# ── Minimal stub shim ──────────────────────────────────────────────────────────


class _StubShim(BaseShim):
    @property
    def device_id(self) -> str:
        return "stub.0"

    def start(self) -> bool:
        return True

    def stop(self) -> bool:
        return True

    def restart(self) -> bool:
        return True

    def self_test(self) -> dict:
        return {"passed": True, "details": "stub"}

    def rollback(self) -> None:
        pass


# ── NotificationDispatcher tests ──────────────────────────────────────────────


class TestNotificationDispatcherFilter:
    def test_default_level_when_idle(self, tmp_path):
        dispatcher = NotificationDispatcher(device_home=tmp_path, is_busy_fn=lambda: False)
        level, reason = dispatcher.filter("anyone")
        assert level == DeliveryMode.QUIET
        assert "default" in reason

    def test_silent_when_busy(self, tmp_path):
        dispatcher = NotificationDispatcher(device_home=tmp_path, is_busy_fn=lambda: True)
        level, reason = dispatcher.filter("anyone")
        assert level == DeliveryMode.SILENT
        assert "busy" in reason

    def test_override_wins_over_busy(self, tmp_path):
        NotificationConfig(
            default_level=DeliveryMode.QUIET,
            overrides={"akien": DeliveryMode.LOUD},
        ).save(tmp_path)
        dispatcher = NotificationDispatcher(device_home=tmp_path, is_busy_fn=lambda: True)
        level, reason = dispatcher.filter("akien")
        assert level == DeliveryMode.LOUD
        assert "override:akien" in reason

    def test_no_is_busy_fn_uses_config_default(self, tmp_path):
        NotificationConfig(default_level=DeliveryMode.SILENT).save(tmp_path)
        dispatcher = NotificationDispatcher(device_home=tmp_path)
        level, _ = dispatcher.filter("granny")
        assert level == DeliveryMode.SILENT


class TestNotificationDispatcherDeliver:
    def test_silent_delivery_no_side_effects(self, tmp_path):
        dispatcher = NotificationDispatcher(device_home=tmp_path, is_busy_fn=lambda: True)
        result = dispatcher.deliver("igor", "status update")
        assert result == DeliveryMode.SILENT
        assert not (tmp_path / _PENDING_FILENAME).exists()

    def test_quiet_delivery_queues_pending(self, tmp_path):
        dispatcher = NotificationDispatcher(device_home=tmp_path, is_busy_fn=lambda: False)
        dispatcher.deliver("granny", "ticket dispatched")
        pending = (tmp_path / _PENDING_FILENAME)
        assert pending.exists()
        content = pending.read_text()
        assert "granny" in content
        assert "ticket dispatched" in content

    def test_loud_no_session_falls_back_to_quiet(self, tmp_path):
        NotificationConfig(
            default_level=DeliveryMode.QUIET,
            overrides={"akien": DeliveryMode.LOUD},
        ).save(tmp_path)
        dispatcher = NotificationDispatcher(
            device_home=tmp_path,
            tmux_session="nonexistent-session-xyz",
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)  # has-session fails
            result = dispatcher.deliver("akien", "urgent message")
        assert result == DeliveryMode.QUIET
        # Still queued since fell back to QUIET
        assert (tmp_path / _PENDING_FILENAME).exists()

    def test_loud_with_session_sends_tmux(self, tmp_path):
        NotificationConfig(
            default_level=DeliveryMode.LOUD,
        ).save(tmp_path)
        dispatcher = NotificationDispatcher(
            device_home=tmp_path,
            tmux_session="test-session",
            is_busy_fn=lambda: False,
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            result = dispatcher.deliver("akien", "/sprint T-xyz")
        assert result == DeliveryMode.LOUD
        # has-session was called
        calls = [str(c) for c in mock_run.call_args_list]
        assert any("has-session" in c for c in calls)
        assert any("send-keys" in c for c in calls)


class TestNotificationDispatcherDrainPending:
    def test_drain_empty(self, tmp_path):
        dispatcher = NotificationDispatcher(device_home=tmp_path)
        assert dispatcher.drain_pending() == []

    def test_drain_returns_entries_and_clears(self, tmp_path):
        dispatcher = NotificationDispatcher(device_home=tmp_path, is_busy_fn=lambda: False)
        dispatcher.deliver("granny", "msg one")
        dispatcher.deliver("igor", "msg two")
        entries = dispatcher.drain_pending()
        assert len(entries) == 2
        senders = {e["sender"] for e in entries}
        assert senders == {"granny", "igor"}
        # File cleared
        assert not (tmp_path / _PENDING_FILENAME).exists()
        # Second drain returns empty
        assert dispatcher.drain_pending() == []


# ── BaseShim.filter_incoming integration ──────────────────────────────────────


class TestBaseShimFilterIncoming:
    def test_no_notifier_returns_quiet(self):
        shim = _StubShim()
        assert shim._notifier is None
        result = shim.filter_incoming("granny", "hello")
        assert result == DeliveryMode.QUIET

    def test_with_notifier_delegates(self, tmp_path):
        shim = _StubShim()
        shim._notifier = NotificationDispatcher(
            device_home=tmp_path, is_busy_fn=lambda: True
        )
        result = shim.filter_incoming("granny", "hello")
        assert result == DeliveryMode.SILENT

    def test_notifier_is_class_level_none_by_default(self):
        shim1 = _StubShim()
        shim2 = _StubShim()
        assert shim1._notifier is None
        assert shim2._notifier is None
        # Setting on instance does not affect class or other instances
        shim1._notifier = MagicMock()
        assert shim2._notifier is None
