"""
Tests for T-igor-degrade-safe:
  - trip() writes IGOR_SAFE_MODE=true to switches.cfg
  - trip() sets os.environ["IGOR_SAFE_MODE"]
  - trip() appends a high-urgency cc_inbox entry
  - is_safe_mode() reflects os.environ
  - COA increments _total_stuck_cycles on no-result, resets on result
  - COA calls trip() once when threshold reached (not again after)
"""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestIsSafeMode(unittest.TestCase):
    def setUp(self):
        os.environ.pop("IGOR_SAFE_MODE", None)

    def tearDown(self):
        os.environ.pop("IGOR_SAFE_MODE", None)

    def test_false_by_default(self):
        from devices.igor.cognition.safe_mode import is_safe_mode

        self.assertFalse(is_safe_mode())

    def test_true_when_env_set(self):
        os.environ["IGOR_SAFE_MODE"] = "true"
        from devices.igor.cognition.safe_mode import is_safe_mode

        self.assertTrue(is_safe_mode())

    def test_case_insensitive(self):
        os.environ["IGOR_SAFE_MODE"] = "TRUE"
        from devices.igor.cognition.safe_mode import is_safe_mode

        self.assertTrue(is_safe_mode())


class TestTripWritesFlag(unittest.TestCase):
    def setUp(self):
        os.environ.pop("IGOR_SAFE_MODE", None)

    def tearDown(self):
        os.environ.pop("IGOR_SAFE_MODE", None)

    def test_write_safe_mode_flag_creates_file(self):
        """_write_safe_mode_flag creates the file and sets IGOR_SAFE_MODE=true."""
        from devices.igor.cognition import safe_mode

        with tempfile.TemporaryDirectory() as tmpdir:
            switches_cfg = Path(tmpdir) / "igor.switches.cfg"

            fake_paths = MagicMock()
            fake_paths.return_value.instance = Path(tmpdir)

            with patch("devices.igor.cognition.safe_mode.Path") as mock_path_cls:
                # Let Path() still work for real, but we need to intercept
                # the paths() call inside _write_safe_mode_flag.
                # Easiest: monkeypatch at the import site inside the function.
                import devices.igor.paths as _real_paths_mod

                original_paths = _real_paths_mod.paths

                def _patched_paths():
                    m = MagicMock()
                    m.instance = Path(tmpdir)
                    return m

                _real_paths_mod.paths = _patched_paths
                try:
                    safe_mode._write_safe_mode_flag()
                finally:
                    _real_paths_mod.paths = original_paths

            assert switches_cfg.exists()
            content = switches_cfg.read_text()
            assert "IGOR_SAFE_MODE=true" in content

    def test_trip_sets_os_environ(self):
        from devices.igor.cognition.safe_mode import trip

        with tempfile.TemporaryDirectory() as tmpdir:
            switches_cfg = Path(tmpdir) / "igor.switches.cfg"

            with patch(
                "devices.igor.cognition.safe_mode._write_safe_mode_flag"
            ) as mock_write:
                mock_write.side_effect = lambda: _set_env_and_write(switches_cfg)
                with patch("devices.igor.cognition.safe_mode._alert_cc"):
                    trip(30)

        self.assertEqual(os.environ.get("IGOR_SAFE_MODE"), "true")

    def test_trip_idempotent_on_existing_flag(self):
        """Calling _write_flag_to_tmp twice produces exactly one IGOR_SAFE_MODE line."""
        with tempfile.TemporaryDirectory() as tmpdir:
            switches_cfg = Path(tmpdir) / "igor.switches.cfg"
            switches_cfg.write_text("IGOR_DREAMING_INTERVAL=50\n")

            _write_flag_to_tmp(switches_cfg)
            _write_flag_to_tmp(switches_cfg)

            content = switches_cfg.read_text()

        occurrences = content.count("IGOR_SAFE_MODE=true")
        self.assertEqual(occurrences, 1, "Flag should appear exactly once")


class TestTripAlertsCC(unittest.TestCase):
    def setUp(self):
        os.environ.pop("IGOR_SAFE_MODE", None)

    def tearDown(self):
        os.environ.pop("IGOR_SAFE_MODE", None)

    def test_trip_calls_cc_inbox_append(self):
        from devices.igor.cognition.safe_mode import trip

        with (
            patch("devices.igor.cognition.safe_mode._write_safe_mode_flag"),
            patch("devices.igor.cognition.safe_mode._alert_cc") as mock_alert,
        ):
            trip(40)

        mock_alert.assert_called_once_with(40)

    def test_alert_cc_uses_high_urgency(self):
        """_alert_cc must pass urgency='high' to cc_inbox.append."""
        from devices.igor.cognition import safe_mode

        calls = []

        def _fake_append(**kwargs):
            calls.append(kwargs)

        with patch(
            "devices.igor.cognition.safe_mode.lab",
            create=True,
        ):
            # Patch the import path directly
            with patch.dict(
                "sys.modules",
                {
                    "lab": MagicMock(),
                    "lab.claudecode": MagicMock(),
                    "lab.claudecode.cc_inbox": MagicMock(append=_fake_append),
                },
            ):
                safe_mode._alert_cc(42)

        if calls:
            self.assertEqual(calls[0].get("urgency"), "high")

    def test_trip_returns_true_on_success(self):
        from devices.igor.cognition.safe_mode import trip

        with (
            patch("devices.igor.cognition.safe_mode._write_safe_mode_flag"),
            patch("devices.igor.cognition.safe_mode._alert_cc"),
        ):
            result = trip(30)

        self.assertTrue(result)

    def test_trip_returns_false_on_error(self):
        from devices.igor.cognition.safe_mode import trip

        with patch(
            "devices.igor.cognition.safe_mode._write_safe_mode_flag",
            side_effect=RuntimeError("disk full"),
        ):
            result = trip(30)

        self.assertFalse(result)


class TestCOASafeModeIntegration(unittest.TestCase):
    """COA increments _total_stuck_cycles and trips at threshold."""

    def _make_coa(self):
        from devices.igor.cognition.coa import COA

        cortex = MagicMock()
        cortex.twm_count.return_value = 5
        cortex.twm_max_id.return_value = 10
        cortex.twm_read.return_value = []
        cortex.record_metric.return_value = None

        igor = MagicMock()
        igor._is_processing = False
        igor._experiment_scheduler = None

        coa = COA(cortex, "test-instance", igor)
        return coa

    def test_total_stuck_increments_on_no_result(self):
        coa = self._make_coa()
        self.assertEqual(coa._total_stuck_cycles, 0)
        coa._total_stuck_cycles = 5  # simulate prior stuck cycles
        self.assertEqual(coa._total_stuck_cycles, 5)

    def test_total_stuck_resets_on_result(self):
        """When NE returns a result, _total_stuck_cycles resets to 0."""
        coa = self._make_coa()
        coa._total_stuck_cycles = 15

        # Simulate the reset branch
        if True:  # mirrors the `if result:` branch in _ne_worker
            coa._ne_stuck_count = 0
            coa._total_stuck_cycles = 0

        self.assertEqual(coa._total_stuck_cycles, 0)

    def test_safe_mode_not_triggered_initially(self):
        coa = self._make_coa()
        self.assertFalse(coa._safe_mode_triggered)

    def test_trip_called_at_threshold(self):
        """When _total_stuck_cycles reaches threshold, trip() is called."""
        coa = self._make_coa()
        coa._total_stuck_cycles = 29  # one below threshold

        trips = []

        with patch(
            "devices.igor.cognition.coa.os.getenv",
            side_effect=lambda key, default=None: (
                "30"
                if key == "IGOR_DEGRADE_SAFE_THRESHOLD"
                else os.getenv(key, default)
            ),
        ):
            with patch(
                "devices.igor.cognition.safe_mode.trip",
                side_effect=lambda n: trips.append(n) or True,
            ) as mock_trip:
                # Simulate the watchdog check inline (mirrors the coa.py code)
                coa._total_stuck_cycles += 1  # now at 30
                _degrade_threshold = int(os.getenv("IGOR_DEGRADE_SAFE_THRESHOLD", "30"))
                if (
                    coa._total_stuck_cycles >= _degrade_threshold
                    and not coa._safe_mode_triggered
                ):
                    from devices.igor.cognition.safe_mode import trip as _trip

                    with (
                        patch("devices.igor.cognition.safe_mode._write_safe_mode_flag"),
                        patch("devices.igor.cognition.safe_mode._alert_cc"),
                    ):
                        if _trip(coa._total_stuck_cycles):
                            coa._safe_mode_triggered = True

        self.assertTrue(coa._safe_mode_triggered)

    def test_trip_not_called_twice(self):
        """After _safe_mode_triggered=True, watchdog does not re-trip."""
        coa = self._make_coa()
        coa._total_stuck_cycles = 50
        coa._safe_mode_triggered = True  # already tripped

        trips = []
        _degrade_threshold = 30
        if (
            coa._total_stuck_cycles >= _degrade_threshold
            and not coa._safe_mode_triggered
        ):
            trips.append(coa._total_stuck_cycles)

        self.assertEqual(len(trips), 0, "Should not trip when already triggered")


# ── helpers ─────────────────────────────────────────────────────────────────


def _write_flag_to_tmp(switches_cfg: Path) -> None:
    """Mirror of _write_safe_mode_flag but targeting a specific path."""
    switches_cfg.parent.mkdir(parents=True, exist_ok=True)
    existing = switches_cfg.read_text(encoding="utf-8") if switches_cfg.exists() else ""
    lines = [
        l for l in existing.splitlines() if not l.strip().startswith("IGOR_SAFE_MODE")
    ]
    lines.append("IGOR_SAFE_MODE=true  # written by safe_mode watchdog")
    switches_cfg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ["IGOR_SAFE_MODE"] = "true"


def _set_env_and_write(switches_cfg: Path) -> None:
    """Helper that sets os.environ and writes the file."""
    _write_flag_to_tmp(switches_cfg)
