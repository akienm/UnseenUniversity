"""
test_homeostatic.py — T-homeostatic-setpoints

Verifies that after many tick() calls with no update() calls, arousal and
valence converge toward their homeostatic setpoints (not zero), and that
dominance continues to converge toward 0.3 (unchanged).

All file I/O and global-state side-effects are mocked out so the tests run
without any ~/.TheIgors runtime dir.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


import devices.igor.cognition.milieu as milieu_mod
from devices.igor.cognition.milieu import (
    DECAY_AROUSAL,
    DECAY_DOMINANCE,
    DECAY_VALENCE,
    HOMEOSTATIC_AROUSAL_SETPOINT,
    HOMEOSTATIC_VALENCE_SETPOINT,
    MilieuState,
)

# Enough ticks for all three dims to land within 0.01 of their setpoints.
# Worst case: dominance (DECAY=0.99) — after N ticks the error is 0.99^N.
# 0.99^700 ≈ 0.0008, which is well inside 0.01.
MANY_TICKS = 700


def _make_milieu(
    tmpdir: Path, valence=0.0, arousal=0.0, dominance=0.0
) -> milieu_mod.Milieu:
    """
    Build a Milieu whose _state is set to (valence, arousal, dominance) and
    whose file I/O + global contributions are fully mocked.
    """
    state_path = tmpdir / "milieu.json"
    # Pre-write a valid state file so __init__ loads our initial values.
    import json

    s = MilieuState(valence=valence, arousal=arousal, dominance=dominance)
    state_path.write_text(
        json.dumps({k: getattr(s, k) for k in s.__dataclass_fields__}), encoding="utf-8"
    )

    with (
        patch.object(milieu_mod, "paths") as mock_paths,
        patch.object(milieu_mod, "_contribute_to_global"),
        patch.object(
            milieu_mod, "_global_milieu_path", return_value=tmpdir / "global.json"
        ),
    ):
        mock_instance = MagicMock()
        mock_instance.__truediv__ = lambda self, other: tmpdir / other
        mock_paths.return_value.instance = tmpdir

        m = milieu_mod.Milieu.__new__(milieu_mod.Milieu)
        # Minimal init without calling __init__ (avoids IgorBase setup).
        m._instance_id = "test"
        m._path = state_path
        m._history_path = tmpdir / "milieu_history.json"
        m._state = m._load()
        m._history = []
        m._session_samples = []
        m._tick_count = 0

    return m


class TestHomeostaticSetpoints(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmpdir = Path(tempfile.mkdtemp())

    # ── Convergence from below setpoint ───────────────────────────────────────

    def test_arousal_converges_from_zero(self):
        """Starting at 0, arousal should rise to ~HOMEOSTATIC_AROUSAL_SETPOINT."""
        m = _make_milieu(self._tmpdir, arousal=0.0)
        with (
            patch.object(m, "_save"),
            patch.object(milieu_mod, "_contribute_to_global"),
            patch.object(m, "_read_global", return_value=None),
        ):
            for _ in range(MANY_TICKS):
                m.tick()
        self.assertAlmostEqual(
            m._state.arousal, HOMEOSTATIC_AROUSAL_SETPOINT, delta=0.01
        )

    def test_valence_converges_from_zero(self):
        """Starting at 0, valence should rise to ~HOMEOSTATIC_VALENCE_SETPOINT."""
        m = _make_milieu(self._tmpdir, valence=0.0)
        with (
            patch.object(m, "_save"),
            patch.object(milieu_mod, "_contribute_to_global"),
            patch.object(m, "_read_global", return_value=None),
        ):
            for _ in range(MANY_TICKS):
                m.tick()
        self.assertAlmostEqual(
            m._state.valence, HOMEOSTATIC_VALENCE_SETPOINT, delta=0.01
        )

    # ── Convergence from above setpoint ───────────────────────────────────────

    def test_arousal_decays_from_high(self):
        """Starting at 0.8, arousal should decay toward HOMEOSTATIC_AROUSAL_SETPOINT."""
        m = _make_milieu(self._tmpdir, arousal=0.8)
        with (
            patch.object(m, "_save"),
            patch.object(milieu_mod, "_contribute_to_global"),
            patch.object(m, "_read_global", return_value=None),
        ):
            for _ in range(MANY_TICKS):
                m.tick()
        self.assertAlmostEqual(
            m._state.arousal, HOMEOSTATIC_AROUSAL_SETPOINT, delta=0.01
        )

    def test_valence_decays_from_high(self):
        """Starting at 0.9, valence should decay toward HOMEOSTATIC_VALENCE_SETPOINT."""
        m = _make_milieu(self._tmpdir, valence=0.9)
        with (
            patch.object(m, "_save"),
            patch.object(milieu_mod, "_contribute_to_global"),
            patch.object(m, "_read_global", return_value=None),
        ):
            for _ in range(MANY_TICKS):
                m.tick()
        self.assertAlmostEqual(
            m._state.valence, HOMEOSTATIC_VALENCE_SETPOINT, delta=0.01
        )

    # ── Dominance unchanged ───────────────────────────────────────────────────

    def test_dominance_still_converges_to_0_3(self):
        """Dominance setpoint is hard-coded 0.3 — verify unchanged."""
        m = _make_milieu(self._tmpdir, dominance=0.0)
        with (
            patch.object(m, "_save"),
            patch.object(milieu_mod, "_contribute_to_global"),
            patch.object(m, "_read_global", return_value=None),
        ):
            for _ in range(MANY_TICKS):
                m.tick()
        self.assertAlmostEqual(m._state.dominance, 0.3, delta=0.01)

    def test_dominance_decays_from_high_to_0_3(self):
        """Dominance starting at 0.9 should converge toward 0.3."""
        m = _make_milieu(self._tmpdir, dominance=0.9)
        with (
            patch.object(m, "_save"),
            patch.object(milieu_mod, "_contribute_to_global"),
            patch.object(m, "_read_global", return_value=None),
        ):
            for _ in range(MANY_TICKS):
                m.tick()
        self.assertAlmostEqual(m._state.dominance, 0.3, delta=0.01)

    # ── Does NOT converge to zero ─────────────────────────────────────────────

    def test_arousal_does_not_converge_to_zero(self):
        """Old behaviour: arousal × DECAY → 0. New behaviour: floor at setpoint."""
        m = _make_milieu(self._tmpdir, arousal=0.0)
        with (
            patch.object(m, "_save"),
            patch.object(milieu_mod, "_contribute_to_global"),
            patch.object(m, "_read_global", return_value=None),
        ):
            for _ in range(MANY_TICKS):
                m.tick()
        self.assertGreater(m._state.arousal, 0.0)

    def test_valence_does_not_converge_to_zero(self):
        """Old behaviour: valence × DECAY → 0. New behaviour: floor at setpoint."""
        m = _make_milieu(self._tmpdir, valence=0.0)
        with (
            patch.object(m, "_save"),
            patch.object(milieu_mod, "_contribute_to_global"),
            patch.object(m, "_read_global", return_value=None),
        ):
            for _ in range(MANY_TICKS):
                m.tick()
        self.assertGreater(m._state.valence, 0.0)


if __name__ == "__main__":
    unittest.main()
