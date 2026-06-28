"""
test_web_server_device.py — WebServerDevice unit tests.

Tests the BaseDevice contract and health-check logic without requiring
the server to actually be running (mock-based). Integration health test
runs only when port 8080 is live.
"""

import json
import unittest
from unittest.mock import MagicMock, patch

from unseen_university.devices.web_server.device import WebServerDevice, _check_health
from unseen_university.devices.web_server.shim import WebServerShim
from unseen_university.device import INTERFACE_VERSION


class TestWebServerDeviceContract(unittest.TestCase):
    def setUp(self):
        self.d = WebServerDevice()

    def test_who_am_i_has_required_keys(self):
        info = self.d.who_am_i()
        self.assertIn("device_id", info)
        self.assertIn("name", info)
        self.assertIn("version", info)
        self.assertEqual(info["device_id"], "web-server")

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
        import time

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
        with patch("unseen_university.devices.web_server.device._check_health", return_value=None):
            self.d.block("test reason")
            h = self.d.health()
            self.assertEqual(h["status"], "unhealthy")
            self.assertIn("test reason", h["detail"])

    def test_recovery_clears_block(self):
        self.d.block("blocked")
        self.d.recovery()
        self.assertFalse(self.d._blocked)


class TestCheckHealth(unittest.TestCase):
    def test_returns_none_when_no_server(self):
        # Point at a port nothing is on
        with (
            patch("unseen_university.devices.web_server.device._PORT", 19999),
            patch("unseen_university.devices.web_server.device._HTTP_PORT", 19998),
        ):
            result = _check_health()
            self.assertIsNone(result)

    def test_returns_dict_when_server_healthy(self):
        fake_response = json.dumps({"status": "ok", "uptime_s": 10}).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = fake_response
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _check_health()
            self.assertIsNotNone(result)
            self.assertEqual(result["status"], "ok")


class TestWebServerShimContract(unittest.TestCase):
    def test_device_id(self):
        s = WebServerShim()
        self.assertEqual(s.device_id, "web-server")

    def test_self_test_reflects_health(self):
        s = WebServerShim()
        with patch("unseen_university.devices.web_server.shim._check_health", return_value=None):
            result = s.self_test()
            self.assertFalse(result["passed"])

        fake = {"status": "ok"}
        with patch("unseen_university.devices.web_server.shim._check_health", return_value=fake):
            result = s.self_test()
            self.assertTrue(result["passed"])

    def test_rollback_calls_stop(self):
        s = WebServerShim()
        s._device.stop = MagicMock()
        s.rollback()
        s._device.stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
