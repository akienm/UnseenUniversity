"""Tests for devices/web_server/screenshot_capture.py — headless screenshot capture."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.web_server.screenshot_capture import (
    _chrome_bin,
    capture_device,
    screenshot_path,
)


# ── _chrome_bin ────────────────────────────────────────────────────────────────

def test_chrome_bin_returns_path_when_found():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="/usr/bin/google-chrome\n")
        result = _chrome_bin()
        assert result == "/usr/bin/google-chrome"


def test_chrome_bin_returns_none_when_not_found():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = _chrome_bin()
        assert result is None


# ── capture_device ─────────────────────────────────────────────────────────────

def test_capture_device_returns_none_when_no_chrome(tmp_path):
    with patch("unseen_university.devices.web_server.screenshot_capture._chrome_bin", return_value=None):
        result = capture_device("igor", out_dir=tmp_path)
        assert result is None


def test_capture_device_returns_path_on_success(tmp_path):
    screenshot = tmp_path / "igor.png"
    screenshot.write_bytes(b"\x89PNG\r\n")  # fake PNG

    def _fake_run(cmd, **kwargs):
        # Simulate chrome writing the file
        out_path = next((c for c in cmd if c.startswith("--screenshot=")), None)
        if out_path:
            Path(out_path.split("=", 1)[1]).write_bytes(b"\x89PNG\r\n")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("unseen_university.devices.web_server.screenshot_capture._chrome_bin", return_value="/usr/bin/google-chrome"):
        with patch("subprocess.run", side_effect=_fake_run):
            result = capture_device("igor", base_url="http://127.0.0.1:8080", out_dir=tmp_path)
            assert result is not None
            assert result.exists()


def test_capture_device_returns_none_on_chrome_failure(tmp_path):
    with patch("unseen_university.devices.web_server.screenshot_capture._chrome_bin", return_value="/usr/bin/google-chrome"):
        with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="failed")):
            result = capture_device("igor", out_dir=tmp_path)
            assert result is None


def test_capture_device_uses_correct_url(tmp_path):
    calls = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return MagicMock(returncode=1)

    with patch("unseen_university.devices.web_server.screenshot_capture._chrome_bin", return_value="/usr/bin/chrome"):
        with patch("subprocess.run", side_effect=_fake_run):
            capture_device("nanny-ogg", base_url="http://127.0.0.1:9999", out_dir=tmp_path)

    assert calls
    url_arg = calls[0][-1]
    assert "127.0.0.1:9999" in url_arg
    assert "/feeds/nanny-ogg" in url_arg


# ── screenshot_path ─────────────────────────────────────────────────────────────

def test_screenshot_path_returns_png_path(tmp_path):
    path = screenshot_path("igor", out_dir=tmp_path)
    assert path == tmp_path / "igor.png"


def test_screenshot_path_uses_default_dir_when_none():
    from unseen_university.devices.web_server.screenshot_capture import _SCREENSHOT_DIR
    path = screenshot_path("granny")
    assert path.name == "granny.png"
    assert "screenshots" in str(path)


# ── Nanny schedule entry ───────────────────────────────────────────────────────

def test_nanny_default_schedule_includes_screenshot_capture():
    from unseen_university.devices.nanny.device import _DEFAULT_SCHEDULE
    ids = [e["entry_id"] for e in _DEFAULT_SCHEDULE]
    assert "periodic_screenshot_capture" in ids
    entry = next(e for e in _DEFAULT_SCHEDULE if e["entry_id"] == "periodic_screenshot_capture")
    assert entry["action_type"] == "run_screenshot_capture"
    assert entry["condition_params"].get("interval_hours") == 1


def test_nanny_fire_entry_handles_screenshot_capture():
    from unseen_university.devices.nanny.device import NannyOggDevice, ScheduleEntry

    device = NannyOggDevice()
    entry = ScheduleEntry(
        entry_id="test_screenshot",
        condition_type="cron",
        condition_params={},
        action_type="run_screenshot_capture",
        action_params={},
    )

    capture_called = []

    def _fake_capture(*args, **kwargs):
        capture_called.append(True)
        return {"igor": True, "granny": False}

    with patch("unseen_university.devices.web_server.screenshot_capture.capture_all", _fake_capture):
        with patch.object(device, "_post_to_channel"):
            ok = device.fire_entry(entry)

    assert ok is True
    assert capture_called
