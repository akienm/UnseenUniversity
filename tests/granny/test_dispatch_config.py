"""Tests for devices.granny.dispatch_config — gate evaluator and config reader."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

# ── helpers ───────────────────────────────────────────────────────────────────


def _ctx(**kw):
    return kw


def _never_available(_worker_id: str) -> bool:
    return False


def _always_available(_worker_id: str) -> bool:
    return True


# ── time_window gate ──────────────────────────────────────────────────────────


class TestTimeWindowGate:
    def _eval(self, spec, hour, minute=0):
        from devices.granny.dispatch_config import evaluate_gate

        now = datetime(2026, 6, 2, hour, minute)
        return evaluate_gate("time_window", spec, _ctx(now=now), _always_available)

    def test_inside_daytime_window(self):
        assert self._eval("09:00-17:00", 12) is True

    def test_outside_daytime_window_before(self):
        assert self._eval("09:00-17:00", 8) is False

    def test_outside_daytime_window_after(self):
        assert self._eval("09:00-17:00", 18) is False

    def test_inside_overnight_window_before_midnight(self):
        assert self._eval("21:00-06:00", 22) is True

    def test_inside_overnight_window_after_midnight(self):
        assert self._eval("21:00-06:00", 2) is True

    def test_outside_overnight_window(self):
        assert self._eval("21:00-06:00", 10) is False

    def test_boundary_at_start(self):
        assert self._eval("21:00-06:00", 21, 0) is True

    def test_none_value_passes(self):
        from devices.granny.dispatch_config import evaluate_gate

        assert evaluate_gate("time_window", None, _ctx(), _always_available) is True


# ── usage_max_pct gate ────────────────────────────────────────────────────────


class TestUsageMaxPctGate:
    def _eval(self, max_pct, current):
        from devices.granny.dispatch_config import evaluate_gate

        return evaluate_gate(
            "usage_max_pct", max_pct, _ctx(usage_pct=current), _always_available
        )

    def test_below_threshold_passes(self):
        assert self._eval(70, 50.0) is True

    def test_at_threshold_fails(self):
        assert self._eval(70, 70.0) is False

    def test_above_threshold_fails(self):
        assert self._eval(70, 90.0) is False

    def test_zero_usage_passes(self):
        assert self._eval(70, 0.0) is True

    def test_none_value_passes(self):
        from devices.granny.dispatch_config import evaluate_gate

        assert (
            evaluate_gate("usage_max_pct", None, _ctx(usage_pct=99), _always_available)
            is True
        )


# ── semaphore gates ───────────────────────────────────────────────────────────


class TestSemaphoreGate:
    def _eval_away(self, spec, avail):
        from devices.granny.dispatch_config import evaluate_gate

        fn = lambda wid: avail  # noqa: E731
        return evaluate_gate("away_semaphore", spec, _ctx(), fn)

    def _eval_available(self, spec, avail):
        from devices.granny.dispatch_config import evaluate_gate

        fn = lambda wid: avail  # noqa: E731
        return evaluate_gate("available_semaphore", spec, _ctx(), fn)

    def test_away_semaphore_present_passes(self):
        assert self._eval_away("CC.0.available.true", True) is True

    def test_away_semaphore_absent_fails(self):
        assert self._eval_away("CC.0.available.true", False) is False

    def test_available_semaphore_present_passes(self):
        assert self._eval_available("DickSimnel.0.available.true", True) is True

    def test_available_semaphore_absent_fails(self):
        assert self._eval_available("DickSimnel.0.available.true", False) is False

    def test_semaphore_spec_strips_suffix(self):
        from devices.granny.dispatch_config import _semaphore_worker_id

        assert _semaphore_worker_id("CC.0.available.true") == "CC.0"
        assert _semaphore_worker_id("DickSimnel.0.available.true") == "DickSimnel.0"
        assert _semaphore_worker_id("granny.queue.available.false") == "granny.queue"


# ── max_concurrent gate ───────────────────────────────────────────────────────


class TestMaxConcurrentGate:
    def _eval(self, max_c, busy):
        from devices.granny.dispatch_config import evaluate_gate

        return evaluate_gate(
            "max_concurrent", max_c, _ctx(cc0_busy=busy), _always_available
        )

    def test_not_busy_passes(self):
        assert self._eval(1, False) is True

    def test_busy_fails(self):
        assert self._eval(1, True) is False

    def test_none_value_passes(self):
        from devices.granny.dispatch_config import evaluate_gate

        assert (
            evaluate_gate(
                "max_concurrent", None, _ctx(cc0_busy=True), _always_available
            )
            is True
        )


# ── evaluate_worker_gates ────────────────────────────────────────────────────


class TestEvaluateWorkerGates:
    def test_all_gates_pass(self):
        from devices.granny.dispatch_config import evaluate_worker_gates

        config = {
            "dispatch": "tmux",
            "gates": {
                "time_window": "21:00-06:00",
                "usage_max_pct": 70,
                "max_concurrent": 1,
            },
        }
        ctx = _ctx(now=datetime(2026, 6, 2, 22, 0), usage_pct=40.0, cc0_busy=False)
        assert (
            evaluate_worker_gates("CC.0", config, ctx, semaphore_fn=_always_available)
            is True
        )

    def test_one_gate_fails_short_circuits(self):
        from devices.granny.dispatch_config import evaluate_worker_gates

        config = {
            "gates": {
                "time_window": "21:00-06:00",
                "usage_max_pct": 70,
            }
        }
        # Outside time window
        ctx = _ctx(now=datetime(2026, 6, 2, 12, 0), usage_pct=40.0)
        assert (
            evaluate_worker_gates("CC.0", config, ctx, semaphore_fn=_always_available)
            is False
        )

    def test_empty_gates_passes(self):
        from devices.granny.dispatch_config import evaluate_worker_gates

        assert (
            evaluate_worker_gates("CC.0", {}, _ctx(), semaphore_fn=_always_available)
            is True
        )

    def test_semaphore_injected(self):
        from devices.granny.dispatch_config import evaluate_worker_gates

        config = {"gates": {"away_semaphore": "CC.0.available.true"}}
        assert (
            evaluate_worker_gates("CC.0", config, _ctx(), semaphore_fn=_never_available)
            is False
        )
        assert (
            evaluate_worker_gates(
                "CC.0", config, _ctx(), semaphore_fn=_always_available
            )
            is True
        )


# ── config reader ─────────────────────────────────────────────────────────────


class TestLoadDispatchConfig:
    def test_loads_granny_yaml(self, tmp_path, monkeypatch):
        from devices.granny.dispatch_config import load_dispatch_config
        import devices.granny.dispatch_config as dc

        cfg = tmp_path / "granny.yaml"
        cfg.write_text("workers:\n  CC.0:\n    dispatch: tmux\n")
        monkeypatch.setattr(dc, "_CONFIG_PATH", cfg)
        result = load_dispatch_config()
        assert "workers" in result
        assert "CC.0" in result["workers"]

    def test_returns_empty_when_missing(self, tmp_path, monkeypatch):
        from devices.granny.dispatch_config import load_dispatch_config
        import devices.granny.dispatch_config as dc

        monkeypatch.setattr(dc, "_CONFIG_PATH", tmp_path / "nonexistent.yaml")
        result = load_dispatch_config()
        assert result == {"workers": {}}

    def test_get_worker_config(self, tmp_path, monkeypatch):
        from devices.granny.dispatch_config import get_worker_config

        config = {
            "workers": {
                "CC.0": {"dispatch": "tmux"},
                "DickSimnel.0": {"dispatch": "channel"},
            }
        }
        assert get_worker_config("CC.0", config)["dispatch"] == "tmux"
        assert get_worker_config("Igor", config) is None


# ── cc.yaml profile / concurrency mode ────────────────────────────────────────


class TestCcConcurrencyMode:
    def test_reads_cc0_only_from_profile(self, tmp_path):
        from devices.granny.dispatch_config import get_cc_concurrency_mode

        cc_yaml = tmp_path / "cc.yaml"
        cc_yaml.write_text("cc_concurrency_mode: cc0_only\n")
        assert get_cc_concurrency_mode(cc_yaml) == "cc0_only"

    def test_reads_cc0_plus_cc1_from_profile(self, tmp_path):
        from devices.granny.dispatch_config import get_cc_concurrency_mode

        cc_yaml = tmp_path / "cc.yaml"
        cc_yaml.write_text("cc_concurrency_mode: cc0_plus_cc1\n")
        assert get_cc_concurrency_mode(cc_yaml) == "cc0_plus_cc1"

    def test_defaults_to_cc0_only_when_missing(self, tmp_path):
        from devices.granny.dispatch_config import get_cc_concurrency_mode

        assert get_cc_concurrency_mode(tmp_path / "nonexistent.yaml") == "cc0_only"

    def test_defaults_to_cc0_only_when_field_absent(self, tmp_path):
        from devices.granny.dispatch_config import get_cc_concurrency_mode

        cc_yaml = tmp_path / "cc.yaml"
        cc_yaml.write_text("profile_version: '1.0'\nagent_type: cc\n")
        assert get_cc_concurrency_mode(cc_yaml) == "cc0_only"

    def test_cc0_only_blocks_second_dispatch(self, tmp_path, monkeypatch):
        """cc0_only mode: _cc0_available returns False when another CC is in_progress."""
        import devices.granny.daemon as d

        cc_yaml = tmp_path / "cc.yaml"
        cc_yaml.write_text("cc_concurrency_mode: cc0_only\n")

        granny_yaml = tmp_path / "granny.yaml"
        granny_yaml.write_text(
            "workers:\n  CC.0:\n    dispatch: tmux\n    gates:\n      usage_max_pct: 90\n"
        )

        import devices.granny.dispatch_config as dc
        monkeypatch.setattr(dc, "_CONFIG_PATH", granny_yaml)
        monkeypatch.setattr(dc, "_CC_PROFILE_PATH", cc_yaml)
        monkeypatch.setattr(d, "_get_usage_pct", lambda: 0.0)
        monkeypatch.setattr(d, "_cc0_in_progress", lambda: True)  # busy!
        # Semaphore: no away/available gates in this config so availability.is_available not called
        assert d._cc0_available() is False

    def test_cc0_only_allows_when_not_busy(self, tmp_path, monkeypatch):
        import devices.granny.daemon as d

        cc_yaml = tmp_path / "cc.yaml"
        cc_yaml.write_text("cc_concurrency_mode: cc0_only\n")

        granny_yaml = tmp_path / "granny.yaml"
        granny_yaml.write_text(
            "workers:\n  CC.0:\n    dispatch: tmux\n    gates:\n      usage_max_pct: 90\n"
        )

        import devices.granny.dispatch_config as dc
        monkeypatch.setattr(dc, "_CONFIG_PATH", granny_yaml)
        monkeypatch.setattr(dc, "_CC_PROFILE_PATH", cc_yaml)
        monkeypatch.setattr(d, "_get_usage_pct", lambda: 0.0)
        monkeypatch.setattr(d, "_cc0_in_progress", lambda: False)
        assert d._cc0_available() is True
