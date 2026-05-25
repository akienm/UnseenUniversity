"""
test_network_proxy.py — Tests for T-network-proxy: NetworkProxy + HostStats.

T-igor-network-remove: network/proxy.py removed. Tests skipped until proxy
relocates to unseen_university or another module.

Tests:
  - HostStats.record() increments counts correctly
  - HostStats error_rate
  - HostStats percentiles (p50, p95)
  - HostStats sample cap (never exceeds _MAX_SAMPLES)
  - HostStats.to_dict() keys
  - NetworkProxy._extract_host() URL parsing
  - NetworkProxy.host_stats() sorted by call_count descending
  - NetworkProxy.get() success path (mocked urlopen)
  - NetworkProxy.get() failure path (urlopen raises)
  - NetworkProxy.post() tracks stats
  - NetworkProxy.post_json() parses JSON response
  - NetworkProxy.post_json() returns None on invalid JSON
  - NetworkProxy.report_str() format with no calls
  - NetworkProxy.report_str() format with calls
  - get_network_proxy_report tool smoke test
"""

import pytest

pass  # T-igor-channels-relocate: proxy moved to wild_igor/igor/tools/network_proxy.py

import json
import sys
import unittest
import unittest.mock as mock
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))

from igor.tools.network_proxy import HostStats, NetworkProxy, _extract_host

# ── HostStats unit tests ──────────────────────────────────────────────────────


class TestHostStats(unittest.TestCase):
    def test_initial_state(self):
        s = HostStats(host="example.com")
        self.assertEqual(s.call_count, 0)
        self.assertEqual(s.error_count, 0)
        self.assertEqual(s.error_rate, 0.0)
        self.assertIsNone(s.p50)
        self.assertIsNone(s.p95)

    def test_record_success(self):
        s = HostStats(host="example.com")
        s.record(50, True)
        self.assertEqual(s.call_count, 1)
        self.assertEqual(s.error_count, 0)
        self.assertEqual(s.error_rate, 0.0)
        self.assertEqual(s.p50, 50)

    def test_record_error(self):
        s = HostStats(host="example.com")
        s.record(50, False)
        self.assertEqual(s.call_count, 1)
        self.assertEqual(s.error_count, 1)
        self.assertAlmostEqual(s.error_rate, 1.0)

    def test_error_rate_mixed(self):
        s = HostStats(host="example.com")
        s.record(10, True)
        s.record(20, False)
        s.record(30, True)
        s.record(40, False)
        self.assertAlmostEqual(s.error_rate, 0.5)

    def test_p50_and_p95(self):
        s = HostStats(host="example.com")
        for ms in range(1, 101):  # 100 samples: 1..100
            s.record(ms, True)
        # idx=max(0, int(100*95/100)-1)=94 → sorted[94]=95
        self.assertEqual(s.p95, 95)
        self.assertIsNotNone(s.p50)

    def test_sample_cap(self):
        s = HostStats(host="example.com")
        for i in range(700):
            s.record(i, True)
        self.assertLessEqual(len(s._samples), HostStats._MAX_SAMPLES)
        self.assertEqual(s.call_count, 700)

    def test_to_dict_keys(self):
        s = HostStats(host="example.com")
        s.record(30, True)
        d = s.to_dict()
        for key in ("host", "calls", "errors", "error_rate", "p50_ms", "p95_ms"):
            self.assertIn(key, d)
        self.assertEqual(d["host"], "example.com")


# ── _extract_host tests ───────────────────────────────────────────────────────


class TestExtractHost(unittest.TestCase):
    def test_https_url(self):
        self.assertEqual(
            _extract_host("https://openrouter.ai/api/v1/chat"), "openrouter.ai"
        )

    def test_http_with_port(self):
        self.assertEqual(_extract_host("http://localhost:11434/api/embed"), "localhost")

    def test_bare_url(self):
        # No scheme → falls back gracefully
        result = _extract_host("openrouter.ai/foo")
        self.assertIn("openrouter.ai", result)


# ── NetworkProxy unit tests ───────────────────────────────────────────────────


def _make_mock_resp(body: bytes, status: int = 200):
    """Return a context-manager mock for urlopen."""
    resp = mock.MagicMock()
    resp.read.return_value = body
    resp.status = status
    resp.__enter__ = mock.MagicMock(return_value=resp)
    resp.__exit__ = mock.MagicMock(return_value=False)
    return resp


class TestNetworkProxy(unittest.TestCase):
    def test_get_success(self):
        px = NetworkProxy()
        mock_resp = _make_mock_resp(b'{"ok": true}')
        with mock.patch(
            "igor.tools.network_proxy.urllib.request.urlopen", return_value=mock_resp
        ):
            result = px.get("https://example.com/ping")
        self.assertEqual(result, b'{"ok": true}')
        stats = px.host_stats()
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["host"], "example.com")
        self.assertEqual(stats[0]["calls"], 1)
        self.assertEqual(stats[0]["errors"], 0)

    def test_get_failure_returns_none(self):
        px = NetworkProxy()
        with mock.patch(
            "igor.tools.network_proxy.urllib.request.urlopen",
            side_effect=OSError("connection refused"),
        ):
            result = px.get("https://down.example.com/ping")
        self.assertIsNone(result)
        stats = px.host_stats()
        self.assertEqual(stats[0]["calls"], 1)
        self.assertEqual(stats[0]["errors"], 1)
        self.assertAlmostEqual(stats[0]["error_rate"], 1.0)

    def test_post_tracks_stats(self):
        px = NetworkProxy()
        mock_resp = _make_mock_resp(b"pong")
        with mock.patch(
            "igor.tools.network_proxy.urllib.request.urlopen", return_value=mock_resp
        ):
            result = px.post("https://api.example.com/do", b'{"x":1}')
        self.assertEqual(result, b"pong")
        stats = px.host_stats()
        self.assertEqual(stats[0]["calls"], 1)
        self.assertEqual(stats[0]["errors"], 0)

    def test_post_json_parses_response(self):
        px = NetworkProxy()
        payload = {"answer": 42}
        mock_resp = _make_mock_resp(json.dumps(payload).encode())
        with mock.patch(
            "igor.tools.network_proxy.urllib.request.urlopen", return_value=mock_resp
        ):
            result = px.post_json("https://api.example.com/json", {"q": "hi"})
        self.assertEqual(result, {"answer": 42})

    def test_post_json_returns_none_on_bad_json(self):
        px = NetworkProxy()
        mock_resp = _make_mock_resp(b"not json at all")
        with mock.patch(
            "igor.tools.network_proxy.urllib.request.urlopen", return_value=mock_resp
        ):
            result = px.post_json("https://api.example.com/bad", {})
        self.assertIsNone(result)

    def test_post_json_returns_none_on_error(self):
        px = NetworkProxy()
        with mock.patch(
            "igor.tools.network_proxy.urllib.request.urlopen",
            side_effect=OSError("timeout"),
        ):
            result = px.post_json("https://api.example.com/err", {})
        self.assertIsNone(result)

    def test_multiple_hosts_sorted_by_calls(self):
        px = NetworkProxy()
        mock_resp_a = _make_mock_resp(b"a")
        mock_resp_b = _make_mock_resp(b"b")
        with mock.patch(
            "igor.tools.network_proxy.urllib.request.urlopen", return_value=mock_resp_a
        ):
            px.get("https://alpha.com/x")
        with mock.patch(
            "igor.tools.network_proxy.urllib.request.urlopen", return_value=mock_resp_b
        ):
            px.get("https://beta.com/x")
            px.get("https://beta.com/y")
        stats = px.host_stats()
        # beta.com has 2 calls, alpha.com has 1 → beta first
        self.assertEqual(stats[0]["host"], "beta.com")
        self.assertEqual(stats[1]["host"], "alpha.com")

    def test_latency_recorded(self):
        px = NetworkProxy()
        mock_resp = _make_mock_resp(b"ok")
        with mock.patch(
            "igor.tools.network_proxy.urllib.request.urlopen", return_value=mock_resp
        ):
            px.get("https://example.com/ping")
        stats = px.host_stats()
        self.assertIsNotNone(stats[0]["p50_ms"])
        self.assertGreaterEqual(stats[0]["p50_ms"], 0)


# ── report_str tests ──────────────────────────────────────────────────────────


class TestNetworkProxyReport(unittest.TestCase):
    def test_no_calls_message(self):
        px = NetworkProxy()
        msg = px.report_str()
        self.assertIn("no outbound calls", msg)

    def test_report_with_calls(self):
        px = NetworkProxy()
        mock_resp = _make_mock_resp(b"ok")
        with mock.patch(
            "igor.tools.network_proxy.urllib.request.urlopen", return_value=mock_resp
        ):
            px.get("https://openrouter.ai/test")
        report = px.report_str()
        self.assertIn("openrouter.ai", report)
        self.assertIn("1x", report)
        self.assertIn("err=0%", report)

    def test_report_shows_errors(self):
        px = NetworkProxy()
        with mock.patch(
            "igor.tools.network_proxy.urllib.request.urlopen",
            side_effect=OSError("down"),
        ):
            px.get("https://broken.example.com/ping")
        report = px.report_str()
        self.assertIn("broken.example.com", report)
        self.assertIn("err=100%", report)


# ── get_network_proxy_report tool smoke test ──────────────────────────────────


class TestGetNetworkProxyReportTool(unittest.TestCase):
    def test_returns_string(self):
        from igor.tools.metrics import _get_network_proxy_report

        result = _get_network_proxy_report()
        self.assertIsInstance(result, str)

    def test_no_calls_message_via_tool(self):
        """Patching global proxy to a fresh instance verifies the no-calls path."""
        from igor.tools import metrics as metrics_mod
        import igor.tools.network_proxy as proxy_mod

        fresh_proxy = NetworkProxy()
        original = proxy_mod.proxy
        proxy_mod.proxy = fresh_proxy
        try:
            from igor.tools.metrics import _get_network_proxy_report

            result = _get_network_proxy_report()
            self.assertIn("no outbound calls", result)
        finally:
            proxy_mod.proxy = original


if __name__ == "__main__":
    unittest.main()
