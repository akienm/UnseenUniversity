"""Tests for IgorADCShim ADC lifecycle management."""

import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from devices.igor.web.adc_shim import IgorADCShim, _check_health


class TestCheckHealth:
    """Tests for _check_health() utility."""

    def test_check_health_returns_true_on_valid_response(self):
        """Verify _check_health() returns True when /health responds."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.read.return_value = b'{"status": "ok"}'
            mock_response.__enter__.return_value = mock_response
            mock_response.__exit__.return_value = None
            mock_urlopen.return_value = mock_response

            result = _check_health(timeout_s=3.0)
            assert result is True

    def test_check_health_returns_false_on_timeout(self):
        """Verify _check_health() returns False when /health times out."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            import urllib.error

            mock_urlopen.side_effect = urllib.error.URLError("timeout")

            result = _check_health(timeout_s=3.0)
            assert result is False

    def test_check_health_returns_false_on_connection_error(self):
        """Verify _check_health() returns False when connection fails."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            import urllib.error

            mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

            result = _check_health(timeout_s=3.0)
            assert result is False


class TestIgorADCShimBasics:
    """Tests for IgorADCShim basic properties and methods."""

    def test_device_id_property(self):
        """Verify device_id property returns correct identifier."""
        shim = IgorADCShim()
        assert shim.device_id == "adc-web-server"

    def test_self_test_when_health_check_passes(self):
        """Verify self_test() returns {passed: True} when ADC responds."""
        with patch("devices.igor.web.adc_shim._check_health") as mock_check:
            mock_check.return_value = True

            shim = IgorADCShim()
            result = shim.self_test()

            assert result["passed"] is True
            assert "responding" in result["details"]

    def test_self_test_when_health_check_fails(self):
        """Verify self_test() returns {passed: False} when ADC doesn't respond."""
        with patch("devices.igor.web.adc_shim._check_health") as mock_check:
            mock_check.return_value = False

            shim = IgorADCShim()
            result = shim.self_test()

            assert result["passed"] is False
            assert "not responding" in result["details"]

    def test_self_test_handles_exception(self):
        """Verify self_test() handles exceptions gracefully."""
        with patch("devices.igor.web.adc_shim._check_health") as mock_check:
            mock_check.side_effect = RuntimeError("forced error")

            shim = IgorADCShim()
            result = shim.self_test()

            assert result["passed"] is False
            assert "error" in result["details"]


class TestIgorADCShimStartStopRestart:
    """Tests for IgorADCShim lifecycle methods."""

    def test_start_returns_true_when_adc_already_running(self):
        """Verify start() returns True if ADC already responds to /health."""
        with patch("devices.igor.web.adc_shim._check_health") as mock_check:
            mock_check.return_value = True

            shim = IgorADCShim()
            result = shim.start()

            assert result is True
            assert shim._owns_process is False

    def test_start_returns_false_on_launch_failure(self):
        """Verify start() returns False if subprocess launch fails."""
        with (
            patch("devices.igor.web.adc_shim._check_health") as mock_check,
            patch("subprocess.Popen") as mock_popen,
        ):
            mock_check.return_value = False  # ADC not running
            mock_popen.side_effect = FileNotFoundError("server script not found")

            shim = IgorADCShim()
            result = shim.start()

            assert result is False

    def test_start_returns_false_on_health_poll_timeout(self):
        """Verify start() returns False if /health doesn't respond within 15s."""
        with (
            patch("devices.igor.web.adc_shim._check_health") as mock_check,
            patch("subprocess.Popen") as mock_popen,
            patch("time.time") as mock_time,
            patch("time.sleep"),
        ):
            # First call: ADC not running; subsequent calls: still not responding
            mock_check.side_effect = [False, False, False, False, False]
            mock_process = MagicMock()
            mock_process.pid = 1234
            mock_process.poll.return_value = None  # Process still running
            mock_popen.return_value = mock_process

            # Simulate time passing beyond deadline
            call_count = [0]

            def fake_time():
                call_count[0] += 1
                return float(call_count[0] * 16)  # Jump past 15s deadline

            mock_time.side_effect = fake_time

            shim = IgorADCShim()
            result = shim.start()

            assert result is False

    def test_stop_returns_true_when_no_process_to_stop(self):
        """Verify stop() returns True when Igor doesn't own ADC process."""
        shim = IgorADCShim()
        assert shim._owns_process is False
        result = shim.stop()
        assert result is True

    def test_stop_terminates_owned_process(self):
        """Verify stop() terminates the ADC process if Igor owns it."""
        shim = IgorADCShim()
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Process running
        shim._process = mock_process
        shim._owns_process = True

        result = shim.stop()

        assert result is True
        mock_process.terminate.assert_called_once()
        mock_process.wait.assert_called()
        assert shim._process is None
        assert shim._owns_process is False

    def test_restart_calls_stop_and_start(self):
        """Verify restart() calls stop() then start()."""
        with (
            patch.object(IgorADCShim, "stop") as mock_stop,
            patch.object(IgorADCShim, "start") as mock_start,
        ):
            mock_stop.return_value = True
            mock_start.return_value = True

            shim = IgorADCShim()
            result = shim.restart()

            assert result is True
            mock_stop.assert_called_once()
            mock_start.assert_called_once()


class TestIgorADCShimRollback:
    """Tests for IgorADCShim rollback on start failure."""

    def test_rollback_kills_process_if_owned(self):
        """Verify rollback() kills the subprocess if Igor owns it."""
        shim = IgorADCShim()
        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Process running
        shim._process = mock_process
        shim._owns_process = True

        shim.rollback()

        mock_process.kill.assert_called_once()
        assert shim._process is None
        assert shim._owns_process is False

    def test_rollback_no_op_if_not_owned(self):
        """Verify rollback() is a no-op if Igor doesn't own the process."""
        shim = IgorADCShim()
        shim._owns_process = False
        # Should not raise
        shim.rollback()

    def test_rollback_handles_timeout(self):
        """Verify rollback() handles SIGKILL timeout gracefully."""
        shim = IgorADCShim()
        mock_process = MagicMock()
        mock_process.poll.return_value = None
        mock_process.kill.side_effect = subprocess.TimeoutExpired("cmd", 5.0)
        shim._process = mock_process
        shim._owns_process = True

        # Should not raise despite timeout
        shim.rollback()

        assert shim._owns_process is False
