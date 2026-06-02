"""Tests for BaseShim.health_surface(), check_circuit(), and _post_status()."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from unseen_university.shim import BaseShim


class _MinimalShim(BaseShim):
    """Minimal concrete shim for testing BaseShim default behaviour."""

    @property
    def device_id(self) -> str:
        return "test-device"

    def start(self) -> bool:
        return True

    def stop(self) -> bool:
        return True

    def restart(self) -> bool:
        return True

    def self_test(self) -> dict:
        return {"passed": True, "details": "ok"}

    def rollback(self) -> None:
        pass


class _OverrideShim(_MinimalShim):
    """Shim that adds static health fields via override."""

    def health_surface(self) -> dict[str, str]:
        return {**super().health_surface(), "widget_count": "42"}


# ── health_surface ────────────────────────────────────────────────────────────


class TestHealthSurface:
    def test_default_returns_empty_dict(self):
        assert _MinimalShim().health_surface() == {}

    def test_override_returns_own_fields(self):
        h = _OverrideShim().health_surface()
        assert h["widget_count"] == "42"

    def test_post_status_feeds_into_health_surface(self):
        shim = _MinimalShim()
        with patch("unseen_university.channel.post_to_channel"):
            shim._post_status("active", "yes")
        assert shim.health_surface()["active"] == "yes"

    def test_post_status_merged_with_override_fields(self):
        shim = _OverrideShim()
        with patch("unseen_university.channel.post_to_channel"):
            shim._post_status("dynamic_key", "dyn")
        h = shim.health_surface()
        assert h["widget_count"] == "42"
        assert h["dynamic_key"] == "dyn"

    def test_health_surface_returns_copy(self):
        shim = _MinimalShim()
        with patch("unseen_university.channel.post_to_channel"):
            shim._post_status("k", "v")
        h = shim.health_surface()
        h["k"] = "mutated"
        assert shim.health_surface()["k"] == "v"


# ── check_circuit ─────────────────────────────────────────────────────────────


class TestCheckCircuit:
    def test_returns_false_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("UU_CIRCUIT_STATE_FILE", str(tmp_path / "no_such_file.json"))
        import unseen_university.shim as shim_mod
        from pathlib import Path

        monkeypatch.setattr(
            shim_mod,
            "_CIRCUIT_STATE_FILE",
            Path(tmp_path / "no_such_file.json"),
        )
        assert _MinimalShim().check_circuit() is False

    def test_returns_false_when_device_closed(self, tmp_path, monkeypatch):
        state_file = tmp_path / "circuit_state.json"
        state_file.write_text(json.dumps({"test-device": "CLOSED"}))
        import unseen_university.shim as shim_mod
        from pathlib import Path

        monkeypatch.setattr(shim_mod, "_CIRCUIT_STATE_FILE", Path(state_file))
        assert _MinimalShim().check_circuit() is False

    def test_returns_false_when_device_absent(self, tmp_path, monkeypatch):
        state_file = tmp_path / "circuit_state.json"
        state_file.write_text(json.dumps({"other-device": "OPEN"}))
        import unseen_university.shim as shim_mod
        from pathlib import Path

        monkeypatch.setattr(shim_mod, "_CIRCUIT_STATE_FILE", Path(state_file))
        assert _MinimalShim().check_circuit() is False

    def test_returns_true_when_device_open(self, tmp_path, monkeypatch):
        state_file = tmp_path / "circuit_state.json"
        state_file.write_text(json.dumps({"test-device": "OPEN"}))
        import unseen_university.shim as shim_mod
        from pathlib import Path

        monkeypatch.setattr(shim_mod, "_CIRCUIT_STATE_FILE", Path(state_file))
        assert _MinimalShim().check_circuit() is True

    def test_returns_false_on_corrupt_file(self, tmp_path, monkeypatch):
        state_file = tmp_path / "circuit_state.json"
        state_file.write_text("not-json{{")
        import unseen_university.shim as shim_mod
        from pathlib import Path

        monkeypatch.setattr(shim_mod, "_CIRCUIT_STATE_FILE", Path(state_file))
        assert _MinimalShim().check_circuit() is False


# ── _post_status ──────────────────────────────────────────────────────────────


class TestPostStatus:
    def test_updates_cache(self):
        shim = _MinimalShim()
        with patch("unseen_university.channel.post_to_channel"):
            shim._post_status("status", "ok")
        assert shim.health_surface()["status"] == "ok"

    def test_overwrites_existing_key(self):
        shim = _MinimalShim()
        with patch("unseen_university.channel.post_to_channel"):
            shim._post_status("k", "first")
            shim._post_status("k", "second")
        assert shim.health_surface()["k"] == "second"

    def test_posts_to_channel_with_device_id(self):
        shim = _MinimalShim()
        with patch("unseen_university.channel.post_to_channel") as mock_post:
            shim._post_status("foo", "bar")
        mock_post.assert_called_once_with(
            "foo=bar", author="test-device", channel="test-device"
        )

    def test_channel_failure_does_not_raise(self):
        shim = _MinimalShim()
        with patch(
            "unseen_university.channel.post_to_channel",
            side_effect=Exception("channel down"),
        ):
            shim._post_status("resilient", "yes")
        assert shim.health_surface()["resilient"] == "yes"


# ── GrannyShim.health_surface override ───────────────────────────────────────


class TestGrannyShimHealthSurface:
    def test_returns_at_least_one_field(self):
        from devices.granny.shim import GrannyShim

        shim = GrannyShim()
        h = shim.health_surface()
        assert isinstance(h, dict)
        assert len(h) >= 1

    def test_contains_relaunch_count(self):
        from devices.granny.shim import GrannyShim

        shim = GrannyShim()
        h = shim.health_surface()
        assert "relaunch_count" in h
        assert h["relaunch_count"] == "0"

    def test_contains_daemon_field(self):
        from devices.granny.shim import GrannyShim
        from unittest.mock import MagicMock

        shim = GrannyShim()
        mock_daemon = MagicMock()
        mock_daemon.is_running.return_value = True
        with patch("devices.granny.daemon.get_daemon", return_value=mock_daemon):
            h = shim.health_surface()
        assert "daemon" in h
        assert h["daemon"] == "running"
