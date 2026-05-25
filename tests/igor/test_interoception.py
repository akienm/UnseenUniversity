"""
Tests for T-interoception: InteroceptionSource and Milieu.nudge_vad.

Verifies:
- nudge_vad() applies deltas directly (not EMA blend)
- _compute_vad() maps resource readings to correct VAD signs
- Calm state produces positive valence (positive registration)
- Sustained stress amplifies arousal (temporal accumulation)
- TWM push suppressed when calm; milieu nudge still fires
"""

import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ── milieu.nudge_vad tests ─────────────────────────────────────────────────────


class FakeMilieuState:
    def __init__(self):
        self.valence = 0.0
        self.arousal = 0.0
        self.dominance = 0.3
        self.tick = 0
        self.last_update = 0.0


def _make_milieu(tmp_path):
    """Instantiate a real Milieu without hitting disk or global paths."""
    import importlib
    import os

    # Point paths to tmp_path
    os.environ["IGOR_INSTANCE_DIR"] = str(tmp_path)
    os.environ["IGOR_MILIEU_DIR"] = str(tmp_path)

    from devices.igor.cognition import milieu as milieu_mod
    from devices.igor.cognition.milieu import Milieu

    m = Milieu.__new__(Milieu)
    m._instance_id = "test"
    m._path = tmp_path / "milieu.json"
    m._history_path = tmp_path / "milieu_history.json"
    m._state = FakeMilieuState()
    m._history = []
    m._session_samples = []
    m._tick_count = 0
    return m


class TestNudgeVAD(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmpdir = tempfile.mkdtemp()
        from pathlib import Path

        self._tmp = Path(self._tmpdir)

    def _make(self):
        return _make_milieu(self._tmp)

    def test_nudge_adds_deltas(self):
        """nudge_vad adds deltas directly without EMA remapping."""
        with (
            patch("devices.igor.cognition.milieu.Milieu._save"),
            patch("devices.igor.cognition.milieu._contribute_to_global"),
        ):
            m = self._make()
            m.nudge_vad(0.05, 0.10, -0.03)
            self.assertAlmostEqual(m._state.valence, 0.05, places=4)
            self.assertAlmostEqual(m._state.arousal, 0.10, places=4)
            self.assertAlmostEqual(m._state.dominance, 0.3 - 0.03, places=4)

    def test_nudge_clamps_to_range(self):
        """nudge_vad keeps values within [-1, 1]."""
        with (
            patch("devices.igor.cognition.milieu.Milieu._save"),
            patch("devices.igor.cognition.milieu._contribute_to_global"),
        ):
            m = self._make()
            m._state.valence = 0.95
            m.nudge_vad(0.20, 0.0, 0.0)
            self.assertLessEqual(m._state.valence, 1.0)

    def test_nudge_accumulates_on_repeated_calls(self):
        """Repeated small nudges accumulate (additive, not EMA)."""
        with (
            patch("devices.igor.cognition.milieu.Milieu._save"),
            patch("devices.igor.cognition.milieu._contribute_to_global"),
        ):
            m = self._make()
            for _ in range(5):
                m.nudge_vad(0.0, 0.03, 0.0)
            self.assertAlmostEqual(m._state.arousal, 0.15, places=4)

    def test_nudge_updates_session_samples(self):
        with (
            patch("devices.igor.cognition.milieu.Milieu._save"),
            patch("devices.igor.cognition.milieu._contribute_to_global"),
        ):
            m = self._make()
            m.nudge_vad(0.1, 0.0, 0.0)
            self.assertEqual(len(m._session_samples), 1)


# ── InteroceptionSource._compute_vad tests ────────────────────────────────────


def _make_source():
    """Make an InteroceptionSource without instantiating module singletons."""
    from devices.igor.cognition.push_sources import InteroceptionSource

    src = InteroceptionSource.__new__(InteroceptionSource)
    src._last_run = None
    src._stress_history = []
    return src


class TestComputeVAD(unittest.TestCase):
    def test_calm_produces_positive_valence(self):
        """CPU < 35% + mem < 40% → positive valence, negative arousal delta."""
        src = _make_source()
        dv, da, dd, stress = src._compute_vad(
            cpu=20,
            mem=30,
            disk=50,
            db_latency_ms=10,
            infer_latency_s=0.5,
            cluster_reachable=False,
        )
        self.assertGreater(dv, 0.0, "calm CPU should produce positive valence")
        self.assertLess(da, 0.0, "calm CPU should reduce arousal")

    def test_capable_zone_small_positive_valence(self):
        """CPU 35-60% → small positive valence (system is responsive)."""
        src = _make_source()
        dv, da, dd, stress = src._compute_vad(
            cpu=45,
            mem=50,
            disk=50,
            db_latency_ms=0,
            infer_latency_s=0,
            cluster_reachable=False,
        )
        self.assertGreater(dv, 0.0, "capable CPU should produce small positive valence")

    def test_cpu_overload_raises_arousal_drops_valence(self):
        """CPU > 85% → arousal↑, valence↓, dominance↓."""
        src = _make_source()
        dv, da, dd, stress = src._compute_vad(
            cpu=90,
            mem=50,
            disk=50,
            db_latency_ms=0,
            infer_latency_s=0,
            cluster_reachable=False,
        )
        self.assertGreater(da, 0.0, "high CPU should raise arousal")
        self.assertLess(dv, 0.0, "high CPU should lower valence")
        self.assertLess(dd, 0.0, "high CPU should erode dominance")

    def test_cpu_strain_zone(self):
        """CPU 60-85% → arousal↑, dominance↓."""
        src = _make_source()
        dv, da, dd, stress = src._compute_vad(
            cpu=70,
            mem=50,
            disk=50,
            db_latency_ms=0,
            infer_latency_s=0,
            cluster_reachable=False,
        )
        self.assertGreater(da, 0.0)
        self.assertLess(dd, 0.0)

    def test_high_memory_pressure(self):
        """mem > 90% → strong negative valence."""
        src = _make_source()
        dv_low, _, _, _ = src._compute_vad(
            cpu=30,
            mem=50,
            disk=50,
            db_latency_ms=0,
            infer_latency_s=0,
            cluster_reachable=False,
        )
        dv_high, _, _, _ = src._compute_vad(
            cpu=30,
            mem=92,
            disk=50,
            db_latency_ms=0,
            infer_latency_s=0,
            cluster_reachable=False,
        )
        self.assertLess(
            dv_high, dv_low, "high mem should reduce valence more than low mem"
        )

    def test_db_latency_erodes_dominance(self):
        """db_latency_ms > 200 → dominance↓."""
        src = _make_source()
        _, _, dd_ok, _ = src._compute_vad(
            cpu=30,
            mem=30,
            disk=50,
            db_latency_ms=50,
            infer_latency_s=0,
            cluster_reachable=False,
        )
        _, _, dd_slow, _ = src._compute_vad(
            cpu=30,
            mem=30,
            disk=50,
            db_latency_ms=300,
            infer_latency_s=0,
            cluster_reachable=False,
        )
        self.assertLess(dd_slow, dd_ok, "high db latency should erode dominance more")

    def test_cluster_reachable_boosts_dominance(self):
        """cluster_reachable=True → small positive dominance."""
        src = _make_source()
        _, _, dd_reach, _ = src._compute_vad(
            cpu=30,
            mem=30,
            disk=50,
            db_latency_ms=0,
            infer_latency_s=0,
            cluster_reachable=True,
        )
        _, _, dd_none, _ = src._compute_vad(
            cpu=30,
            mem=30,
            disk=50,
            db_latency_ms=0,
            infer_latency_s=0,
            cluster_reachable=False,
        )
        self.assertGreater(
            dd_reach, dd_none, "cluster reachable should boost dominance"
        )


# ── Temporal accumulation tests ────────────────────────────────────────────────


class TestSustainedArousal(unittest.TestCase):
    def test_no_boost_on_first_sample(self):
        src = _make_source()
        boost = src._sustained_arousal_boost(0.8)
        self.assertEqual(boost, 0.0, "no boost on first high-stress sample")

    def test_boost_after_sustained_high_stress(self):
        src = _make_source()
        for _ in range(4):
            src._sustained_arousal_boost(0.6)  # all above SUSTAIN_THRESHOLD=0.35
        boost = src._sustained_arousal_boost(0.6)
        self.assertGreater(boost, 0.0, "sustained stress should produce arousal boost")

    def test_no_boost_after_recovery(self):
        src = _make_source()
        for _ in range(4):
            src._sustained_arousal_boost(0.6)
        src._sustained_arousal_boost(0.1)  # recovery — below threshold
        boost = src._sustained_arousal_boost(0.6)
        self.assertEqual(boost, 0.0, "streak broken by recovery should reset boost")

    def test_boost_capped(self):
        src = _make_source()
        for _ in range(20):
            src._sustained_arousal_boost(0.9)
        boost = src._sustained_arousal_boost(0.9)
        self.assertLessEqual(boost, src.SUSTAIN_MAX)


# ── Integration: push() milieu nudge even in calm state ───────────────────────


class TestPushCalmNudge(unittest.TestCase):
    def test_calm_nudges_milieu_but_skips_twm(self):
        """Calm state: milieu gets nudge_vad() called, but no TWM push."""
        import sys

        src = _make_source()
        nudge_called = []

        mock_milieu_instance = MagicMock()
        mock_milieu_instance.nudge_vad.side_effect = (
            lambda dv, da, dd: nudge_called.append((dv, da, dd))
        )

        mock_cortex = MagicMock()
        mock_cortex._db = MagicMock()
        mock_cortex._db.get_metrics.return_value = {"latency_p50_ms": 5.0}

        # Patch psutil to return calm values
        mock_psutil = MagicMock()
        mock_psutil.cpu_percent.return_value = 20.0
        mock_psutil.virtual_memory.return_value.percent = 25.0
        mock_psutil.disk_usage.return_value.percent = 40.0

        import devices.igor.cognition.milieu as real_milieu_mod

        with patch.dict(sys.modules, {"psutil": mock_psutil}):
            with patch("devices.igor.cognition.push_sources.MACHINES_JSON", ""):
                with patch.object(
                    real_milieu_mod, "get", return_value=mock_milieu_instance
                ):
                    # Force past rate limiter
                    src._last_run = None
                    result = src.push(mock_cortex)

        # Calm state: salience < MIN_TWM_SALIENCE → TWM not pushed (empty list)
        self.assertEqual(result, [], "calm state should not push to TWM")
        # But milieu nudge_vad should have been called (positive registration)
        self.assertGreater(len(nudge_called), 0, "calm state should still nudge milieu")


if __name__ == "__main__":
    unittest.main()
