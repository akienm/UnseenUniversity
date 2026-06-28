"""Tests for CpuPegMonitor — threshold detection, debounce, and channel post."""

from __future__ import annotations

from collections import deque
from unittest.mock import MagicMock

import pytest

from unseen_university.devices.scraps.jobs.cpu_peg_monitor import CpuPegMonitor


def _make_monitor(threshold=90, samples_needed=3, top=None, posts=None):
    """Build a CpuPegMonitor with injected stubs."""
    top_fn = MagicMock(return_value=top or [])
    post_fn = MagicMock()
    m = CpuPegMonitor(
        threshold=threshold,
        samples_needed=samples_needed,
        sample_interval=5,
        _cpu_fn=None,  # not used when tick() is called with explicit cpu_pct
        _top_fn=top_fn,
        _post_fn=post_fn,
    )
    if posts is not None:
        posts.append(post_fn)
    return m, top_fn, post_fn


# ── Threshold detection ────────────────────────────────────────────────────────


class TestThresholdDetection:
    def test_no_alert_below_threshold(self):
        m, _, post = _make_monitor(threshold=90, samples_needed=3)
        for _ in range(5):
            alerted = m.tick(cpu_pct=50.0)
        assert not alerted
        post.assert_not_called()

    def test_no_alert_on_first_n_minus_one_samples(self):
        m, _, post = _make_monitor(threshold=90, samples_needed=3)
        m.tick(cpu_pct=95.0)
        m.tick(cpu_pct=95.0)
        # Only 2 of 3 required samples in window — no alert yet
        post.assert_not_called()

    def test_alert_fires_after_n_sustained_samples(self):
        m, _, post = _make_monitor(threshold=90, samples_needed=3)
        m.tick(cpu_pct=95.0)
        m.tick(cpu_pct=95.0)
        alerted = m.tick(cpu_pct=95.0)
        assert alerted
        post.assert_called_once()

    def test_alert_message_contains_threshold(self):
        m, _, post = _make_monitor(threshold=85, samples_needed=2)
        m.tick(cpu_pct=90.0)
        m.tick(cpu_pct=90.0)
        msg = post.call_args[0][0]
        assert "threshold=85%" in msg

    def test_alert_message_contains_duration(self):
        m, _, post = _make_monitor(threshold=90, samples_needed=3)
        m.sample_interval = 5
        m.tick(cpu_pct=95.0)
        m.tick(cpu_pct=95.0)
        m.tick(cpu_pct=95.0)
        msg = post.call_args[0][0]
        assert "duration_sec=15" in msg

    def test_no_alert_when_window_has_gap(self):
        m, _, post = _make_monitor(threshold=90, samples_needed=3)
        m.tick(cpu_pct=95.0)
        m.tick(cpu_pct=50.0)  # gap — window reset effectively
        m.tick(cpu_pct=95.0)
        post.assert_not_called()

    def test_exactly_at_threshold_triggers(self):
        m, _, post = _make_monitor(threshold=90, samples_needed=2)
        m.tick(cpu_pct=90.0)
        alerted = m.tick(cpu_pct=90.0)
        assert alerted

    def test_one_below_threshold_in_window_suppresses(self):
        m, _, post = _make_monitor(threshold=90, samples_needed=3)
        m.tick(cpu_pct=95.0)
        m.tick(cpu_pct=89.9)  # just below — even though avg might be above
        m.tick(cpu_pct=95.0)
        post.assert_not_called()


# ── Debounce ──────────────────────────────────────────────────────────────────


class TestDebounce:
    def test_no_repeat_alert_while_still_pegged(self):
        m, _, post = _make_monitor(threshold=90, samples_needed=2)
        m.tick(cpu_pct=95.0)
        m.tick(cpu_pct=95.0)  # alert fires
        m.tick(cpu_pct=95.0)  # should NOT re-alert
        m.tick(cpu_pct=95.0)
        assert post.call_count == 1

    def test_alert_clears_after_cpu_drops(self):
        m, _, post = _make_monitor(threshold=90, samples_needed=2)
        m.tick(cpu_pct=95.0)
        m.tick(cpu_pct=95.0)  # first alert
        m.tick(cpu_pct=50.0)  # cpu drops — latch clears
        m.tick(cpu_pct=95.0)
        m.tick(cpu_pct=95.0)  # second peg event — should alert again
        assert post.call_count == 2

    def test_no_alert_on_single_short_spike(self):
        m, _, post = _make_monitor(threshold=90, samples_needed=3)
        m.tick(cpu_pct=99.0)  # spike
        m.tick(cpu_pct=50.0)  # drops
        m.tick(cpu_pct=99.0)
        m.tick(cpu_pct=50.0)
        post.assert_not_called()


# ── Channel post content ──────────────────────────────────────────────────────


class TestChannelPost:
    def test_top_processes_appear_in_message(self):
        top = [
            {"name": "python3", "pid": 1234, "cpu_percent": 88.0},
            {"name": "node", "pid": 5678, "cpu_percent": 10.0},
        ]
        m, _, post = _make_monitor(threshold=90, samples_needed=2, top=top)
        m.tick(cpu_pct=95.0)
        m.tick(cpu_pct=95.0)
        msg = post.call_args[0][0]
        assert "python3" in msg
        assert "1234" in msg

    def test_post_called_with_string(self):
        m, _, post = _make_monitor(threshold=90, samples_needed=2)
        m.tick(cpu_pct=95.0)
        m.tick(cpu_pct=95.0)
        assert isinstance(post.call_args[0][0], str)

    def test_format_top_handles_empty_list(self):
        m, _, _ = _make_monitor()
        assert m._format_top([]) == "n/a"

    def test_format_top_formats_processes(self):
        m, _, _ = _make_monitor()
        procs = [{"name": "foo", "pid": 42, "cpu_percent": 55.5}]
        result = m._format_top(procs)
        assert "foo" in result
        assert "42" in result
        assert "55%" in result or "56%" in result
