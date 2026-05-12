"""Tests for ClaudeShim ADC auto-start capability."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.error
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

# Must be set before importing ClaudeShim (which imports devices.claude.constants)
os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")

from devices.claude.shim import ClaudeShim, _check_adc_health


class TestCheckAdcHealth:
    """Tests for the _check_adc_health() helper."""

    def test_returns_true_when_health_responds(self):
        with patch("urllib.request.urlopen") as mock_open:
            mock_resp = MagicMock()
            mock_resp.read.return_value = b'{"status": "ok"}'
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = mock_resp
            assert _check_adc_health(timeout_s=3.0) is True

    def test_returns_false_on_url_error(self):
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = urllib.error.URLError("connection refused")
            assert _check_adc_health(timeout_s=3.0) is False

    def test_returns_false_on_any_exception(self):
        with patch("urllib.request.urlopen") as mock_open:
            mock_open.side_effect = RuntimeError("unexpected")
            assert _check_adc_health(timeout_s=3.0) is False


class TestClaudeShimEnsureADCRunning:
    """Tests for ClaudeShim._ensure_adc_running()."""

    def test_returns_true_when_adc_already_up(self):
        """If ADC responds to /health, return True without launching subprocess."""
        with patch("devices.claude.shim._check_adc_health") as mock_health:
            mock_health.return_value = True
            shim = ClaudeShim()
            result = shim._ensure_adc_running()
            assert result is True
            assert shim._adc_owned is False

    def test_returns_false_when_server_script_missing(self, tmp_path):
        """If ADC is down and server script doesn't exist, return False."""
        with (
            patch("devices.claude.shim._check_adc_health") as mock_health,
            patch(
                "devices.claude.shim._ADC_SERVER_PATH",
                str(tmp_path / "nonexistent.py"),
            ),
        ):
            mock_health.return_value = False
            shim = ClaudeShim()
            result = shim._ensure_adc_running()
            assert result is False

    def test_returns_false_when_venv_python_missing(self, tmp_path):
        """If ADC is down and venv Python doesn't exist, return False."""
        fake_server = tmp_path / "utility_closet_server.py"
        fake_server.write_text("# stub")
        with (
            patch("devices.claude.shim._check_adc_health") as mock_health,
            patch("devices.claude.shim._ADC_SERVER_PATH", str(fake_server)),
            patch(
                "devices.claude.shim._ADC_VENV_PYTHON",
                str(tmp_path / "no_python"),
            ),
        ):
            mock_health.return_value = False
            shim = ClaudeShim()
            result = shim._ensure_adc_running()
            assert result is False

    def test_launches_subprocess_when_adc_down(self, tmp_path):
        """If ADC is down and paths exist, subprocess.Popen should be called."""
        fake_server = tmp_path / "utility_closet_server.py"
        fake_server.write_text("# stub")
        fake_python = tmp_path / "python"
        fake_python.write_text("# stub")

        mock_proc = MagicMock()
        mock_proc.pid = 9999

        call_count = [0]

        def health_sequence(timeout_s=3.0):
            call_count[0] += 1
            if call_count[0] == 1:
                return False  # first call: ADC down, triggers launch
            return True  # subsequent calls: ADC came up

        with (
            patch("devices.claude.shim._check_adc_health", side_effect=health_sequence),
            patch("devices.claude.shim._ADC_SERVER_PATH", str(fake_server)),
            patch("devices.claude.shim._ADC_VENV_PYTHON", str(fake_python)),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("time.sleep"),
        ):
            shim = ClaudeShim()
            result = shim._ensure_adc_running()

        assert result is True
        assert shim._adc_owned is True
        assert shim._adc_process is mock_proc

    def test_returns_false_on_health_poll_timeout(self, tmp_path):
        """If health never responds after launch, return False."""
        fake_server = tmp_path / "utility_closet_server.py"
        fake_server.write_text("# stub")
        fake_python = tmp_path / "python"
        fake_python.write_text("# stub")

        mock_proc = MagicMock()
        mock_proc.pid = 9999

        with (
            patch("devices.claude.shim._check_adc_health", return_value=False),
            patch("devices.claude.shim._ADC_SERVER_PATH", str(fake_server)),
            patch("devices.claude.shim._ADC_VENV_PYTHON", str(fake_python)),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("time.sleep"),
            patch("time.monotonic") as mock_time,
        ):
            # Simulate time jumping past the 15s deadline immediately
            mock_time.side_effect = [0.0, 16.0, 16.0]
            shim = ClaudeShim()
            result = shim._ensure_adc_running()

        assert result is False


class TestClaudeShimStartWithADC:
    """Tests for ClaudeShim.start() ADC behavior."""

    def test_start_proceeds_when_adc_up(self, tmp_path):
        """start() registers hook normally when ADC is already up."""
        settings_path = tmp_path / "settings.json"
        with (
            patch("devices.claude.shim._check_adc_health", return_value=True),
            patch("devices.claude.shim._SETTINGS_PATH", str(settings_path)),
        ):
            shim = ClaudeShim()
            result = shim.start()
            assert result is True
            assert settings_path.exists()

    def test_start_proceeds_when_adc_down(self, tmp_path):
        """start() still registers hook even when ADC fails to come up."""
        settings_path = tmp_path / "settings.json"
        with (
            patch("devices.claude.shim._check_adc_health", return_value=False),
            patch(
                "devices.claude.shim._ADC_SERVER_PATH",
                str(tmp_path / "nonexistent.py"),
            ),
            patch("devices.claude.shim._SETTINGS_PATH", str(settings_path)),
        ):
            shim = ClaudeShim()
            result = shim.start()
            # YGM hook must register regardless of ADC status
            assert result is True
            assert settings_path.exists()


class TestClaudeShimSelfTestWithADC:
    """Tests for ClaudeShim.self_test() ADC status inclusion."""

    def test_self_test_includes_adc_reachable_status(self, tmp_path):
        """self_test() details includes 'reachable' when ADC responds."""
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [{"id": "ygm-nudge", "command": "test"}]
                    }
                }
            )
        )
        with (
            patch("devices.claude.shim._check_adc_health", return_value=True),
            patch("devices.claude.shim._SETTINGS_PATH", str(settings_path)),
        ):
            shim = ClaudeShim()
            result = shim.self_test()
            assert result["passed"] is True
            assert "reachable" in result["details"]

    def test_self_test_includes_adc_not_reachable_status(self, tmp_path):
        """self_test() details includes 'not reachable' when ADC is down."""
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "UserPromptSubmit": [{"id": "ygm-nudge", "command": "test"}]
                    }
                }
            )
        )
        with (
            patch("devices.claude.shim._check_adc_health", return_value=False),
            patch("devices.claude.shim._SETTINGS_PATH", str(settings_path)),
        ):
            shim = ClaudeShim()
            result = shim.self_test()
            # Hook is registered, so passed=True, but ADC status noted in details
            assert result["passed"] is True
            assert "not reachable" in result["details"]
