"""
test_tool_registry.py — Tests for T-tool-registry-proxy: ToolStats + ToolRegistry
observability layer.

Tests:
  - ToolStats.record() increments counts correctly
  - ToolStats percentiles (p50, p95)
  - ToolStats error_rate
  - ToolStats sample cap (never exceeds _MAX_SAMPLES)
  - ToolRegistry.execute() tracks stats for successful calls
  - ToolRegistry.execute() tracks stats for erroring calls
  - ToolRegistry.execute() tracks stats for unknown tool (no stats entry)
  - ToolRegistry.tool_stats() sorted by call_count descending
  - _get_tool_registry_report() smoke test
"""

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))

from igor.tools.registry import Tool, ToolRegistry, ToolStats

# ── ToolStats unit tests ──────────────────────────────────────────────────────


class TestToolStats(unittest.TestCase):
    def test_initial_state(self):
        s = ToolStats()
        self.assertEqual(s.call_count, 0)
        self.assertEqual(s.error_count, 0)
        self.assertEqual(s.error_rate, 0.0)
        self.assertIsNone(s.p50)
        self.assertIsNone(s.p95)

    def test_record_success(self):
        s = ToolStats()
        s.record(10, True)
        self.assertEqual(s.call_count, 1)
        self.assertEqual(s.error_count, 0)
        self.assertEqual(s.error_rate, 0.0)
        self.assertEqual(s.p50, 10)

    def test_record_error(self):
        s = ToolStats()
        s.record(10, False)
        self.assertEqual(s.call_count, 1)
        self.assertEqual(s.error_count, 1)
        self.assertAlmostEqual(s.error_rate, 1.0)

    def test_error_rate_mixed(self):
        s = ToolStats()
        s.record(10, True)
        s.record(20, False)
        s.record(30, True)
        s.record(40, False)
        self.assertEqual(s.call_count, 4)
        self.assertEqual(s.error_count, 2)
        self.assertAlmostEqual(s.error_rate, 0.5)

    def test_p50_odd_count(self):
        s = ToolStats()
        for ms in [10, 20, 30, 40, 50]:
            s.record(ms, True)
        # sorted: [10,20,30,40,50], idx = int(5*50/100)-1 = max(0,1) = 1 → 20
        # but with 5 samples: idx = max(0, int(5*50/100)-1) = max(0, 2-1) = 1 → 20
        self.assertIsNotNone(s.p50)
        # p50 should be in the middle-ish
        self.assertIn(s.p50, [10, 20, 30])

    def test_p95(self):
        s = ToolStats()
        for ms in range(1, 101):  # 100 samples: 1..100
            s.record(ms, True)
        # idx = max(0, int(100*95/100)-1) = max(0,94) = 94 → sorted[94] = 95
        self.assertEqual(s.p95, 95)

    def test_sample_cap(self):
        s = ToolStats()
        for i in range(1500):
            s.record(i, True)
        self.assertLessEqual(len(s._samples), ToolStats._MAX_SAMPLES)
        self.assertEqual(s.call_count, 1500)  # count still tracks all

    def test_to_dict_keys(self):
        s = ToolStats()
        s.record(50, True)
        d = s.to_dict()
        self.assertIn("calls", d)
        self.assertIn("errors", d)
        self.assertIn("error_rate", d)
        self.assertIn("p50_ms", d)
        self.assertIn("p95_ms", d)


# ── ToolRegistry observability tests ─────────────────────────────────────────


class TestToolRegistryStats(unittest.TestCase):
    def _make_registry(self):
        """Return a fresh ToolRegistry with two test tools."""
        reg = ToolRegistry()

        reg.register(
            Tool(
                name="ok_tool",
                description="Returns OK",
                parameters={},
                fn=lambda: "OK result",
            )
        )

        def _raise():
            raise RuntimeError("boom")

        reg.register(
            Tool(
                name="fail_tool",
                description="Raises an exception",
                parameters={},
                fn=_raise,
            )
        )
        reg.register(
            Tool(
                name="error_result_tool",
                description="Returns an Error string",
                parameters={},
                fn=lambda: "Error: something went wrong",
            )
        )
        return reg

    def test_success_increments_call_count(self):
        reg = self._make_registry()
        reg.execute("ok_tool", {})
        reg.execute("ok_tool", {})
        stats = reg.tool_stats()
        self.assertEqual(stats["ok_tool"]["calls"], 2)
        self.assertEqual(stats["ok_tool"]["errors"], 0)

    def test_exception_increments_error_count(self):
        reg = self._make_registry()
        result = reg.execute("fail_tool", {})
        self.assertIn("Error", result)
        stats = reg.tool_stats()
        self.assertEqual(stats["fail_tool"]["calls"], 1)
        self.assertEqual(stats["fail_tool"]["errors"], 1)

    def test_error_result_increments_error_count(self):
        reg = self._make_registry()
        reg.execute("error_result_tool", {})
        stats = reg.tool_stats()
        self.assertEqual(stats["error_result_tool"]["calls"], 1)
        self.assertEqual(stats["error_result_tool"]["errors"], 1)

    def test_unknown_tool_no_stats_entry(self):
        reg = self._make_registry()
        result = reg.execute("nonexistent_tool", {})
        self.assertIn("Unknown tool", result)
        stats = reg.tool_stats()
        self.assertNotIn("nonexistent_tool", stats)

    def test_tool_stats_sorted_by_call_count(self):
        reg = self._make_registry()
        # Call ok_tool 3x, error_result_tool 1x
        for _ in range(3):
            reg.execute("ok_tool", {})
        reg.execute("error_result_tool", {})
        stats = reg.tool_stats()
        keys = list(stats.keys())
        self.assertEqual(keys[0], "ok_tool")  # highest count first

    def test_latency_recorded(self):
        reg = self._make_registry()
        reg.execute("ok_tool", {})
        stats = reg.tool_stats()
        self.assertIsNotNone(stats["ok_tool"]["p50_ms"])
        self.assertGreaterEqual(stats["ok_tool"]["p50_ms"], 0)

    def test_empty_stats_initially(self):
        reg = ToolRegistry()
        self.assertEqual(reg.tool_stats(), {})


# ── get_tool_registry_report smoke test ──────────────────────────────────────


class TestGetToolRegistryReport(unittest.TestCase):
    def test_no_calls_message(self):
        """If the global registry has no stats, report says 'No tool calls'."""
        # Import the function but patch the registry it uses
        from igor.tools import metrics as metrics_mod
        from igor.tools.registry import ToolRegistry
        import unittest.mock as mock

        empty_reg = ToolRegistry()
        with mock.patch("igor.tools.metrics.registry", empty_reg):
            # Patch the registry inside _get_tool_registry_report
            import igor.tools.registry as reg_mod

            real_registry = reg_mod.registry
            reg_mod.registry = empty_reg
            try:
                from igor.tools.metrics import _get_tool_registry_report

                result = _get_tool_registry_report()
                self.assertIn("No tool calls", result)
            finally:
                reg_mod.registry = real_registry

    def test_report_format_with_calls(self):
        from igor.tools.registry import ToolRegistry, Tool
        import igor.tools.registry as reg_mod

        test_reg = ToolRegistry()
        test_reg.register(
            Tool(
                name="demo_tool",
                description="Demo",
                parameters={},
                fn=lambda: "demo output",
            )
        )
        test_reg.execute("demo_tool", {})

        original = reg_mod.registry
        reg_mod.registry = test_reg
        try:
            from igor.tools.metrics import _get_tool_registry_report

            result = _get_tool_registry_report()
            self.assertIn("demo_tool", result)
            self.assertIn("1x", result)
        finally:
            reg_mod.registry = original


if __name__ == "__main__":
    unittest.main()
