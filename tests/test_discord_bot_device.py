"""
test_discord_bot_device.py — DiscordBotDevice unit tests (Phase 5).

Tests BaseDevice contract and health logic without requiring a Discord token
or live bot thread.
"""

import os
import time
import unittest
from unittest.mock import patch, MagicMock

from devices.discord_bot.device import DiscordBotDevice
from devices.discord_bot.shim import DiscordBotShim
from unseen_university.device import INTERFACE_VERSION


class TestDiscordBotDeviceContract(unittest.TestCase):
    def setUp(self):
        self.d = DiscordBotDevice()

    def test_who_am_i_has_required_keys(self):
        info = self.d.who_am_i()
        self.assertIn("device_id", info)
        self.assertIn("name", info)
        self.assertIn("version", info)
        self.assertEqual(info["device_id"], "discord-bot")

    def test_requirements_has_deps(self):
        req = self.d.requirements()
        self.assertIn("deps", req)
        self.assertIsInstance(req["deps"], list)

    def test_capabilities_has_required_keys(self):
        cap = self.d.capabilities()
        self.assertIn("can_send", cap)
        self.assertIn("can_receive", cap)
        self.assertIn("emitted_keywords", cap)

    def test_comms_has_required_keys(self):
        comms = self.d.comms()
        self.assertIn("address", comms)
        self.assertIn("mode", comms)
        self.assertIn("supports_push", comms)

    def test_interface_version_matches(self):
        self.assertEqual(self.d.interface_version(), INTERFACE_VERSION)

    def test_uptime_increases(self):
        t1 = self.d.uptime()
        time.sleep(0.05)
        t2 = self.d.uptime()
        self.assertGreater(t2, t1)

    def test_startup_errors_initially_empty(self):
        self.assertEqual(self.d.startup_errors(), [])

    def test_logs_has_paths(self):
        logs = self.d.logs()
        self.assertIn("paths", logs)

    def test_block_sets_unhealthy(self):
        self.d.block("test reason")
        h = self.d.health()
        self.assertEqual(h["status"], "unhealthy")
        self.assertIn("test reason", h["detail"])

    def test_recovery_clears_block(self):
        self.d.block("blocked")
        with patch("devices.discord_bot.device._bot") as mock_bot:
            mock_bot.is_running.return_value = True
            self.d.recovery()
        self.assertFalse(self.d._blocked)

    def test_health_degraded_without_token(self):
        with patch.dict(os.environ, {}, clear=True):
            env = {k: v for k, v in os.environ.items() if k != "DISCORD_BOT_TOKEN"}
            with patch.dict(os.environ, env, clear=True):
                h = self.d.health()
                self.assertEqual(h["status"], "degraded")

    def test_health_unhealthy_when_thread_not_running(self):
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "fake-token"}):
            with patch("devices.discord_bot.device._bot") as mock_bot:
                mock_bot.is_running.return_value = False
                h = self.d.health()
                self.assertEqual(h["status"], "unhealthy")


class TestDiscordBotShimContract(unittest.TestCase):
    def test_device_id(self):
        s = DiscordBotShim()
        self.assertEqual(s.device_id, "discord-bot")

    def test_self_test_fails_without_token(self):
        s = DiscordBotShim()
        env = {k: v for k, v in os.environ.items() if k != "DISCORD_BOT_TOKEN"}
        with patch.dict(os.environ, env, clear=True):
            result = s.self_test()
            self.assertFalse(result["passed"])

    def test_self_test_fails_when_not_running(self):
        s = DiscordBotShim()
        with patch.dict(os.environ, {"DISCORD_BOT_TOKEN": "fake"}):
            with patch("devices.discord_bot.shim._bot") as mock_bot:
                mock_bot.is_running.return_value = False
                result = s.self_test()
                self.assertFalse(result["passed"])

    def test_rollback_calls_stop(self):
        s = DiscordBotShim()
        s._device.stop = MagicMock()
        with patch("devices.discord_bot.shim._bot") as mock_bot:
            mock_bot.stop = MagicMock()
            s.rollback()
        s._device.stop.assert_called_once()

    def test_start_registers_with_skeleton_registry(self):
        from unittest.mock import MagicMock
        mock_registry = MagicMock()
        s = DiscordBotShim(registry=mock_registry)
        with patch("devices.discord_bot.shim._bot") as mock_bot:
            mock_bot.is_running.return_value = False
            with patch("devices.discord_bot.device._bot") as mock_dev_bot:
                mock_dev_bot.is_running.return_value = False
                mock_dev_bot.start = MagicMock()
                s.start()
        mock_registry.register.assert_called_once()
        call_kwargs = mock_registry.register.call_args
        assert call_kwargs.kwargs.get("device_id") == "discord-bot" or call_kwargs.args[0] == "discord-bot"


if __name__ == "__main__":
    unittest.main()
