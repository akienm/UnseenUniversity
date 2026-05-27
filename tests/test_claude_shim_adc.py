"""Tests for ClaudeShim ADC auto-start capability."""

from __future__ import annotations

import json
import os
import urllib.error
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
        """If ADC responds to /health, return True without starting device."""
        with patch("devices.claude.shim._check_adc_health") as mock_health:
            mock_health.return_value = True
            shim = ClaudeShim()
            result = shim._ensure_adc_running()
            assert result is True
            assert shim._adc_owned is False

    def test_starts_web_server_device_when_adc_down(self):
        """If ADC is down, start via WebServerDevice and verify health."""
        mock_dev = MagicMock()
        call_count = [0]

        def health_sequence(timeout_s=3.0):
            call_count[0] += 1
            if call_count[0] == 1:
                return False  # first call: ADC down, triggers device start
            return True  # second call: ADC came up

        with (
            patch("devices.claude.shim._check_adc_health", side_effect=health_sequence),
            patch("devices.web_server.device.WebServerDevice", return_value=mock_dev),
        ):
            shim = ClaudeShim()
            result = shim._ensure_adc_running()

        assert result is True
        assert shim._adc_owned is True
        mock_dev.start.assert_called_once()

    def test_returns_false_when_device_start_raises(self):
        """If WebServerDevice.start() raises, return False."""
        mock_dev = MagicMock()
        mock_dev.start.side_effect = RuntimeError("boom")

        with (
            patch("devices.claude.shim._check_adc_health", return_value=False),
            patch("devices.web_server.device.WebServerDevice", return_value=mock_dev),
        ):
            shim = ClaudeShim()
            result = shim._ensure_adc_running()

        assert result is False

    def test_returns_false_when_health_still_down_after_start(self):
        """If device.start() succeeds but health never comes up, return False."""
        mock_dev = MagicMock()

        with (
            patch("devices.claude.shim._check_adc_health", return_value=False),
            patch("devices.web_server.device.WebServerDevice", return_value=mock_dev),
        ):
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
        mock_dev = MagicMock()
        mock_dev.start.side_effect = RuntimeError("no server")
        settings_path = tmp_path / "settings.json"

        with (
            patch("devices.claude.shim._check_adc_health", return_value=False),
            patch("devices.web_server.device.WebServerDevice", return_value=mock_dev),
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
