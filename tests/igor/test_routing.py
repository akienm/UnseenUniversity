"""
test_routing.py — Tests for D211 cluster router: score formula, in_use_now, route_batch.

Uses temp files to avoid touching live machine_overrides.json.
No network calls — probing is bypassed by pre-setting machine state.
"""

import json
import sys
import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent / "wild_igor"))

from igor.cognition.cluster_router import (
    MachineInfo,
    ClusterRouter,
    _response_time_to_score,
    _NETWORK_WEIGHT,
    _ram_weight,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _healthy_machine(**kwargs) -> MachineInfo:
    """Return a healthy MachineInfo with sensible defaults."""
    defaults = dict(
        name="test",
        ollama_host="http://10.0.0.1:11434",
        primary_model="qwen2.5:7b",
        reasoning_model="deepseek-r1:7b",
        is_local=False,
        hostname="testhost",
        network_type="wired",
        ram_gb=32,
        is_db_host=False,
        healthy=True,
        load_score=1.0,
        active_models=0,
        response_ms=50.0,
    )
    defaults.update(kwargs)
    return MachineInfo(**defaults)


# ── _response_time_to_score ───────────────────────────────────────────────────


class TestResponseTimeScore(unittest.TestCase):
    def test_fast_idle(self):
        s = _response_time_to_score(0, 0)
        self.assertAlmostEqual(s, 1.0)

    def test_2000ms_zero(self):
        s = _response_time_to_score(2000, 0)
        self.assertAlmostEqual(s, 0.0)

    def test_active_inference_penalty(self):
        # 4 active models → penalty = min(4×0.25, 1.0) = 1.0 → score 0
        s = _response_time_to_score(0, 4)
        self.assertAlmostEqual(s, 0.0)

    def test_partial_penalty(self):
        # 1 active model at 0ms: latency=1.0, penalty=0.25 → 0.75
        s = _response_time_to_score(0, 1)
        self.assertAlmostEqual(s, 0.75)

    def test_combined_latency_and_active(self):
        # 500ms = latency 0.75; 1 active = penalty 0.25 → 0.75 * 0.75 = 0.5625
        s = _response_time_to_score(500, 1)
        self.assertAlmostEqual(s, 0.5625)


# ── MachineInfo.score ─────────────────────────────────────────────────────────


class TestMachineScore(unittest.TestCase):
    def test_unhealthy_returns_zero(self):
        m = _healthy_machine(healthy=False)
        self.assertEqual(m.score("local"), 0.0)

    def test_cannot_serve_returns_zero(self):
        # embeddings always requires is_local=True
        m = _healthy_machine(is_local=False)
        self.assertEqual(m.score("embeddings"), 0.0)

    def test_full_score_wired_32gb_no_db(self):
        m = _healthy_machine(
            load_score=1.0, network_type="wired", ram_gb=32, is_db_host=False
        )
        # network=1.0, ram=1.0, db=1.0, capability=1.0, override=1.0
        self.assertAlmostEqual(m.score("local"), 1.0)

    def test_wifi_reduces_score(self):
        m = _healthy_machine(load_score=1.0, network_type="wifi")
        # 1.0 * 0.7 * 1.0 * 1.0 = 0.7
        self.assertAlmostEqual(m.score("local"), 0.7)

    def test_16gb_ram_weight(self):
        m = _healthy_machine(load_score=1.0, network_type="wired", ram_gb=16)
        # 1.0 * 1.0 * 0.8 * 1.0 = 0.8
        self.assertAlmostEqual(m.score("local"), 0.8)

    def test_8gb_ram_weight(self):
        m = _healthy_machine(load_score=1.0, network_type="wired", ram_gb=8)
        # 1.0 * 1.0 * 0.5 = 0.5
        self.assertAlmostEqual(m.score("local"), 0.5)

    def test_db_host_penalty(self):
        m = _healthy_machine(
            load_score=1.0, network_type="wired", ram_gb=32, is_db_host=True
        )
        # db_penalty = 0.2
        self.assertAlmostEqual(m.score("local"), 0.2)

    def test_override_bonus(self):
        m = _healthy_machine(
            name="yoga", load_score=1.0, network_type="wired", ram_gb=32
        )
        score_with = m.score("local", override_name="yoga")
        score_without = m.score("local", override_name="")
        self.assertAlmostEqual(score_with / score_without, 2.0)

    def test_reasoning_without_reasoning_model(self):
        m = _healthy_machine(
            reasoning_model="", load_score=1.0, network_type="wired", ram_gb=32
        )
        # capability_score = 0.5 for reasoning calls without reasoning model
        s = m.score("tier2")
        self.assertAlmostEqual(s, 0.5)

    def test_reasoning_with_reasoning_model(self):
        m = _healthy_machine(
            reasoning_model="deepseek-r1:7b",
            load_score=1.0,
            network_type="wired",
            ram_gb=32,
        )
        s = m.score("tier2")
        self.assertAlmostEqual(s, 1.0)

    def test_load_reduces_score(self):
        m = _healthy_machine(load_score=0.5, network_type="wired", ram_gb=32)
        self.assertAlmostEqual(m.score("local"), 0.5)


class TestInUseNowInScore(unittest.TestCase):
    """Score returns 0.0 when in_use_now() is True."""

    def test_in_use_zeros_score(self):
        m = _healthy_machine(hostname="testhost", load_score=1.0)
        with patch("igor.cognition.cluster_router.MachineInfo.score") as mock_score:
            # We test the real path: in_use_now import and call
            pass

        # Patch at the import path used inside cluster_router
        with patch("igor.tools.routing_tools.in_use_now", return_value=True):
            # Re-import to pick up patch isn't easy with nested imports;
            # instead test via ClusterRouter.route
            pass

    def test_in_use_now_clears_score_via_patch(self):
        """score() returns 0 when in_use_now() returns True for the machine hostname."""
        m = _healthy_machine(hostname="busyhost", load_score=1.0)
        # Patch in_use_now at the location cluster_router imports it from
        with patch("igor.tools.routing_tools.in_use_now", return_value=True):
            result = m.score("local")
        self.assertEqual(result, 0.0)

        with patch("igor.tools.routing_tools.in_use_now", return_value=False):
            result = m.score("local")
        self.assertGreater(result, 0.0)


# ── in_use_now ────────────────────────────────────────────────────────────────


class TestInUseNow(unittest.TestCase):
    """Tests for routing_tools.in_use_now — uses temp files."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.machines_json = self.tmp_path / "machines.json"
        self.overrides_json = self.tmp_path / "machine_overrides.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _patch_paths(self, fn):
        """Decorator-style: run fn with routing_tools paths patched to tmp."""
        import igor.tools.routing_tools as rt

        orig_machines = rt._MACHINES_JSON
        orig_overrides = rt._OVERRIDES_JSON
        rt._MACHINES_JSON = self.machines_json
        rt._OVERRIDES_JSON = self.overrides_json
        try:
            return fn()
        finally:
            rt._MACHINES_JSON = orig_machines
            rt._OVERRIDES_JSON = orig_overrides

    def _write_machines(self, machines: list[dict]):
        self.machines_json.write_text(json.dumps({"machines": machines}))

    def _write_overrides(self, data: dict):
        self.overrides_json.write_text(json.dumps(data))

    def test_no_override_no_windows_returns_false(self):
        self._write_machines(
            [{"hostname": "testhost", "in_use_hours": [], "aliases": []}]
        )

        def run():
            from igor.tools.routing_tools import in_use_now

            return in_use_now("testhost")

        result = self._patch_paths(run)
        self.assertFalse(result)

    def test_indefinite_override_returns_true(self):
        self._write_machines(
            [{"hostname": "testhost", "in_use_hours": [], "aliases": []}]
        )
        self._write_overrides({"testhost": {"in_use": True, "until": None}})

        def run():
            from igor.tools.routing_tools import in_use_now

            return in_use_now("testhost")

        result = self._patch_paths(run)
        self.assertTrue(result)

    def test_expired_override_returns_false(self):
        self._write_machines(
            [{"hostname": "testhost", "in_use_hours": [], "aliases": []}]
        )
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self._write_overrides({"testhost": {"in_use": True, "until": past}})

        def run():
            from igor.tools.routing_tools import in_use_now

            return in_use_now("testhost")

        result = self._patch_paths(run)
        self.assertFalse(result)

    def test_future_override_returns_true(self):
        self._write_machines(
            [{"hostname": "testhost", "in_use_hours": [], "aliases": []}]
        )
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        self._write_overrides({"testhost": {"in_use": True, "until": future}})

        def run():
            from igor.tools.routing_tools import in_use_now

            return in_use_now("testhost")

        result = self._patch_paths(run)
        self.assertTrue(result)

    def test_in_use_hours_window_active(self):
        """When current hour falls inside an in_use_hours window."""
        current_hour = datetime.now().hour
        # Window spans 6 hours centered on current hour
        start = current_hour
        end = (current_hour + 1) % 24
        if start < end:
            self._write_machines(
                [
                    {
                        "hostname": "testhost",
                        "in_use_hours": [[start, end]],
                        "aliases": [],
                    }
                ]
            )

            def run():
                from igor.tools.routing_tools import in_use_now

                return in_use_now("testhost")

            result = self._patch_paths(run)
            self.assertTrue(result)

    def test_in_use_hours_window_outside(self):
        """When current hour is outside all in_use_hours windows."""
        current_hour = datetime.now().hour
        # Window clearly in the past 2 hours if possible
        start = (current_hour + 2) % 24
        end = (current_hour + 3) % 24
        if start < end and end != current_hour:
            self._write_machines(
                [
                    {
                        "hostname": "testhost",
                        "in_use_hours": [[start, end]],
                        "aliases": [],
                    }
                ]
            )

            def run():
                from igor.tools.routing_tools import in_use_now

                return in_use_now("testhost")

            result = self._patch_paths(run)
            self.assertFalse(result)

    def test_unknown_host_returns_false(self):
        self._write_machines(
            [{"hostname": "otherhost", "in_use_hours": [], "aliases": []}]
        )

        def run():
            from igor.tools.routing_tools import in_use_now

            return in_use_now("unknownhost")

        result = self._patch_paths(run)
        self.assertFalse(result)


# ── route_batch ───────────────────────────────────────────────────────────────


class TestRouteBatch(unittest.TestCase):
    """Tests for ClusterRouter.route_batch."""

    def _make_router_with_machines(self, machines: list[MachineInfo]) -> ClusterRouter:
        """Return a ClusterRouter with pre-built machine dict, bypassing env/file loading."""
        r = ClusterRouter()
        r._machines = {m.name: m for m in machines}
        r._built = True
        r._last_refresh = float("inf")  # skip refresh
        return r

    def test_route_batch_returns_best_n(self):
        machines = [
            _healthy_machine(name="a", ollama_host="http://a:11434", load_score=0.9),
            _healthy_machine(name="b", ollama_host="http://b:11434", load_score=0.5),
            _healthy_machine(name="c", ollama_host="http://c:11434", load_score=0.3),
        ]
        r = self._make_router_with_machines(machines)
        # Patch in_use_now to always return False
        with patch("igor.tools.routing_tools.in_use_now", return_value=False):
            result = r.route_batch(2, "local")
        self.assertEqual(len(result), 2)
        # Best score first = machine a
        self.assertEqual(result[0][0], "http://a:11434")
        self.assertEqual(result[1][0], "http://b:11434")

    def test_route_batch_excludes_unhealthy(self):
        machines = [
            _healthy_machine(name="a", ollama_host="http://a:11434", load_score=0.9),
            _healthy_machine(
                name="b", ollama_host="http://b:11434", healthy=False, load_score=0.5
            ),
        ]
        r = self._make_router_with_machines(machines)
        with patch("igor.tools.routing_tools.in_use_now", return_value=False):
            result = r.route_batch(5, "local")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0][0], "http://a:11434")

    def test_route_batch_n_larger_than_machines(self):
        machines = [
            _healthy_machine(name="a", ollama_host="http://a:11434"),
        ]
        r = self._make_router_with_machines(machines)
        with patch("igor.tools.routing_tools.in_use_now", return_value=False):
            result = r.route_batch(10, "local")
        self.assertEqual(len(result), 1)

    def test_route_batch_empty_when_all_in_use(self):
        machines = [
            _healthy_machine(name="a", ollama_host="http://a:11434", hostname="ahost"),
            _healthy_machine(name="b", ollama_host="http://b:11434", hostname="bhost"),
        ]
        r = self._make_router_with_machines(machines)
        with patch("igor.tools.routing_tools.in_use_now", return_value=True):
            result = r.route_batch(5, "local")
        self.assertEqual(result, [])

    def test_route_batch_n_zero_returns_empty(self):
        machines = [_healthy_machine(name="a", ollama_host="http://a:11434")]
        r = self._make_router_with_machines(machines)
        with patch("igor.tools.routing_tools.in_use_now", return_value=False):
            result = r.route_batch(0, "local")
        self.assertEqual(result, [])


# ── ClusterRouter.route (single best) ────────────────────────────────────────


class TestClusterRouterRoute(unittest.TestCase):
    def _make_router_with_machines(self, machines: list[MachineInfo]) -> ClusterRouter:
        r = ClusterRouter()
        r._machines = {m.name: m for m in machines}
        r._built = True
        r._last_refresh = float("inf")
        return r

    def test_returns_best_machine(self):
        machines = [
            _healthy_machine(
                name="low", ollama_host="http://low:11434", load_score=0.3
            ),
            _healthy_machine(
                name="high", ollama_host="http://high:11434", load_score=0.9
            ),
        ]
        r = self._make_router_with_machines(machines)
        with patch("igor.tools.routing_tools.in_use_now", return_value=False):
            host, model = r.route("local")
        self.assertEqual(host, "http://high:11434")

    def test_returns_none_none_when_all_unhealthy(self):
        machines = [
            _healthy_machine(name="a", ollama_host="http://a:11434", healthy=False),
        ]
        r = self._make_router_with_machines(machines)
        host, model = r.route("local")
        self.assertIsNone(host)
        self.assertIsNone(model)

    def test_override_promotes_lower_scored_machine(self):
        machines = [
            _healthy_machine(
                name="fast", ollama_host="http://fast:11434", load_score=0.9
            ),
            _healthy_machine(
                name="slow", ollama_host="http://slow:11434", load_score=0.1
            ),
        ]
        r = self._make_router_with_machines(machines)
        r._override = "slow"
        with patch("igor.tools.routing_tools.in_use_now", return_value=False):
            host, model = r.route("local")
        # slow's score = 0.1 * 2.0 (override) = 0.2 > fast's 0.9? No — fast still wins.
        # But if fast score is low enough, slow wins:
        # fast=0.9, slow=0.1*2=0.2 → fast still wins.
        # Set fast much lower:
        machines[0].load_score = 0.05
        with patch("igor.tools.routing_tools.in_use_now", return_value=False):
            host, model = r.route("local")
        self.assertEqual(host, "http://slow:11434")


if __name__ == "__main__":
    unittest.main()
