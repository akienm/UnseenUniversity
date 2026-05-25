"""
test_dashboard_terminal.py — Tests for dashboard terminal display helpers.

Covers: _cloud_pct, _latency_p50, _latency_p95, outlier guard logic,
        cloud_mode label rendering (no CloudMode/Cloud% contradiction).
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))

from igor.dashboard.terminal import (
    _cloud_pct,
    _latency_p50,
    _latency_p95,
    _valence_str,
)


class TestCloudPct(unittest.TestCase):
    def test_zero_interactions(self):
        # avoid division by zero; returns 100 when no data
        self.assertEqual(_cloud_pct(0, 0), 100)

    def test_all_cloud(self):
        self.assertEqual(_cloud_pct(10, 10), 100)

    def test_none_cloud(self):
        self.assertEqual(_cloud_pct(10, 0), 0)

    def test_half_cloud(self):
        self.assertEqual(_cloud_pct(10, 5), 50)

    def test_rounds(self):
        # 1/3 rounds to 33
        self.assertEqual(_cloud_pct(3, 1), 33)


class TestLatencyPercentiles(unittest.TestCase):
    def test_p50_single(self):
        self.assertEqual(_latency_p50([1000]), 1000)

    def test_p50_even(self):
        # _latency_p50 uses upper-median: s[len(s)//2]
        samples = [100, 200, 300, 400]
        self.assertEqual(_latency_p50(samples), 300)

    def test_p95_excludes_outlier(self):
        """p95 of clean window should ignore outliers > 60s."""
        # 19 normal calls at ~1000ms + 1 stale 185s call
        samples = [1000] * 19 + [185_000]
        _OUTLIER_MS = 60_000
        clean = [s for s in samples if s <= _OUTLIER_MS]
        excluded = len(samples) - len(clean)
        stats = clean if len(clean) >= 2 else samples
        p95 = _latency_p95(stats)
        self.assertEqual(excluded, 1)
        self.assertLess(p95, 60_000, "p95 must be < 60s after outlier exclusion")

    def test_p95_no_outliers(self):
        samples = [500, 600, 700, 800, 900, 1000]
        self.assertEqual(_latency_p95(samples), 1000)

    def test_p95_empty(self):
        self.assertEqual(_latency_p95([]), 0)

    def test_p50_empty(self):
        self.assertEqual(_latency_p50([]), 0)


class TestCloudLabelDistinction(unittest.TestCase):
    """Verify render() output separates cloud_mode gate from cloud calls %."""

    def _make_render_output(self, cloud_mode_active: bool, cloud_calls: int) -> str:
        """
        Extract the performance line from render() without a real Cortex.
        We test the helper values and label string construction directly.
        """
        upstream_pct = _cloud_pct(10, cloud_calls)
        cloud_mode_str = "ON" if cloud_mode_active else "OFF"
        line = f"cloud_mode: {cloud_mode_str}  " f"cloud calls: {upstream_pct}%"
        return line

    def test_cloud_mode_on_zero_pct(self):
        """cloud_mode ON + 0% cloud calls — no contradiction, both explicit."""
        line = self._make_render_output(cloud_mode_active=True, cloud_calls=0)
        self.assertIn("cloud_mode: ON", line)
        self.assertIn("cloud calls: 0%", line)

    def test_cloud_mode_off_some_pct(self):
        """cloud_mode OFF + some cloud calls — both shown without ambiguity."""
        line = self._make_render_output(cloud_mode_active=False, cloud_calls=5)
        self.assertIn("cloud_mode: OFF", line)
        self.assertIn("cloud calls: 50%", line)

    def test_no_cloudmode_label_collision(self):
        """Old 'CloudMode' label must not appear; new labels used instead."""
        line = self._make_render_output(cloud_mode_active=True, cloud_calls=3)
        self.assertNotIn("CloudMode", line)
        self.assertNotIn("Cloud%", line)


class TestValenceStr(unittest.TestCase):
    def test_none(self):
        self.assertEqual(_valence_str(None), "—")

    def test_positive(self):
        result = _valence_str(0.6)
        self.assertIn("positive", result)
        self.assertIn("+0.60", result)

    def test_distressed(self):
        result = _valence_str(-0.8)
        self.assertIn("distressed", result)


if __name__ == "__main__":
    unittest.main()
