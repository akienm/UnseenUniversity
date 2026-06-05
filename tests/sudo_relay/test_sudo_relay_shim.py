"""Tests for SudoRelayDevice and SudoRelayShim."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestSudoRelayDeviceState:
    def test_off_when_no_session(self):
        from devices.sudo_relay.device import SudoRelayDevice

        device = SudoRelayDevice()
        with patch("devices.sudo_relay.device._session_exists", return_value=False):
            assert device.state() == "OFF"

    def test_processing_when_pending_sh_exists(self, tmp_path):
        from devices.sudo_relay.device import SudoRelayDevice

        pending = tmp_path / "pending.sh"
        pending.touch()
        device = SudoRelayDevice()
        with patch("devices.sudo_relay.device._session_exists", return_value=True), \
             patch("devices.sudo_relay.device._PENDING_SH", pending):
            assert device.state() == "PROCESSING"

    def test_needpw_when_password_prompt_visible(self, tmp_path):
        from devices.sudo_relay.device import SudoRelayDevice

        device = SudoRelayDevice()
        with patch("devices.sudo_relay.device._session_exists", return_value=True), \
             patch("devices.sudo_relay.device._PENDING_SH", tmp_path / "nope"), \
             patch("devices.sudo_relay.device._pane_text", return_value="[sudo] password for"):
            assert device.state() == "NEEDPW"

    def test_waiting_when_session_running_no_pending(self, tmp_path):
        from devices.sudo_relay.device import SudoRelayDevice

        device = SudoRelayDevice()
        with patch("devices.sudo_relay.device._session_exists", return_value=True), \
             patch("devices.sudo_relay.device._PENDING_SH", tmp_path / "nope"), \
             patch("devices.sudo_relay.device._pane_text", return_value="watching..."):
            assert device.state() == "WAITING"


class TestSudoRelayDeviceChat:
    def test_free_text_returns_canned_response(self):
        from devices.sudo_relay.device import SudoRelayDevice

        device = SudoRelayDevice()
        with patch("devices.sudo_relay.device._session_exists", return_value=True):
            response = device.handle_chat("hello, how are you?")
        assert "Sorry nice person" in response

    def test_status_command_returns_state(self):
        from devices.sudo_relay.device import SudoRelayDevice

        device = SudoRelayDevice()
        with patch("devices.sudo_relay.device._session_exists", return_value=False):
            response = device.handle_chat("/status")
        assert "OFF" in response

    def test_pw_when_not_needpw_returns_error(self, tmp_path):
        from devices.sudo_relay.device import SudoRelayDevice

        device = SudoRelayDevice()
        with patch("devices.sudo_relay.device._session_exists", return_value=True), \
             patch("devices.sudo_relay.device._PENDING_SH", tmp_path / "nope"), \
             patch("devices.sudo_relay.device._pane_text", return_value="watching..."):
            response = device.handle_chat("/pw mysecret")
        assert "NEEDPW" in response or "not in NEEDPW" in response

    def test_pw_command_not_logged(self):
        """Password must not appear in any log call."""
        from devices.sudo_relay.device import SudoRelayDevice

        device = SudoRelayDevice()
        log_calls = []
        with patch("devices.sudo_relay.device.log") as mock_log, \
             patch("devices.sudo_relay.device._session_exists", return_value=True), \
             patch("devices.sudo_relay.device._pane_text", return_value="[sudo] password:"), \
             patch("devices.sudo_relay.device.subprocess.run", return_value=MagicMock(returncode=0)):
            mock_log.info = lambda *a, **k: log_calls.append(a)
            mock_log.warning = lambda *a, **k: log_calls.append(a)
            device.handle_chat("/pw s3cr3t_p@ss")

        for call_args in log_calls:
            for arg in call_args:
                assert "s3cr3t_p@ss" not in str(arg), "password leaked into log!"

    def test_unknown_slash_returns_canned(self):
        from devices.sudo_relay.device import SudoRelayDevice

        device = SudoRelayDevice()
        response = device.handle_chat("/unknowncommand")
        assert "Sorry nice person" in response


class TestSudoRelayShim:
    def test_start_registers_with_skeleton(self):
        from devices.sudo_relay.shim import SudoRelayShim

        mock_registry = MagicMock()
        shim = SudoRelayShim(registry=mock_registry)
        result = shim.start()

        assert result is True
        mock_registry.register.assert_called_once()
        call_kwargs = mock_registry.register.call_args
        assert call_kwargs.kwargs.get("device_id") == "sudo-relay" or \
               (call_kwargs.args and call_kwargs.args[0] == "sudo-relay")

    def test_self_test_off_when_no_session(self):
        from devices.sudo_relay.shim import SudoRelayShim

        shim = SudoRelayShim(registry=MagicMock())
        with patch("devices.sudo_relay.device._session_exists", return_value=False):
            result = shim.self_test()
        assert result["passed"] is False
        assert "OFF" in result["details"]

    def test_self_test_passed_when_waiting(self, tmp_path):
        from devices.sudo_relay.shim import SudoRelayShim

        shim = SudoRelayShim(registry=MagicMock())
        with patch("devices.sudo_relay.device._session_exists", return_value=True), \
             patch("devices.sudo_relay.device._PENDING_SH", tmp_path / "nope"), \
             patch("devices.sudo_relay.device._pane_text", return_value="waiting..."):
            result = shim.self_test()
        assert result["passed"] is True
        assert "WAITING" in result["details"]

    def test_device_id(self):
        from devices.sudo_relay.shim import SudoRelayShim

        shim = SudoRelayShim(registry=MagicMock())
        assert shim.device_id == "sudo-relay"

    def test_stop_returns_true(self):
        from devices.sudo_relay.shim import SudoRelayShim

        shim = SudoRelayShim(registry=MagicMock())
        assert shim.stop() is True
