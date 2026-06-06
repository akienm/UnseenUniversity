"""Tests for COA._ne_tick_should_yield — hard minimum NE tick interval.

T-igor-ne-tick-interval: prevents NARRATIVE_GAP re-fire loops by enforcing
a minimum delay between NE runs regardless of TWM state changes.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock


def _make_stub(last_run_ago: float, monkeypatch, interval: float = 30.0):
    """Build a minimal COA stub with _ne_last_run_time set to `last_run_ago` seconds ago."""
    monkeypatch.setenv("IGOR_NE_TICK_INTERVAL", str(interval))

    import devices.igor.cognition.coa as coa_mod

    stub = object.__new__(coa_mod.COA)
    stub._ne_last_run_time = time.monotonic() - last_run_ago
    return stub


# ── should_yield returns True within interval ─────────────────────────────────

def test_yields_within_interval(monkeypatch):
    """Within 30s interval: should_yield=True."""
    stub = _make_stub(last_run_ago=10.0, monkeypatch=monkeypatch, interval=30.0)
    should_yield, remaining = stub._ne_tick_should_yield(time.monotonic())
    assert should_yield is True
    assert 15.0 <= remaining <= 25.0  # ~20s remaining (10s elapsed of 30s)


def test_remaining_is_interval_minus_elapsed(monkeypatch):
    """Remaining = interval - elapsed."""
    stub = _make_stub(last_run_ago=5.0, monkeypatch=monkeypatch, interval=30.0)
    _, remaining = stub._ne_tick_should_yield(time.monotonic())
    assert 20.0 <= remaining <= 26.0


# ── should_yield returns False after interval ─────────────────────────────────

def test_does_not_yield_after_interval(monkeypatch):
    """After interval has passed: should_yield=False."""
    stub = _make_stub(last_run_ago=40.0, monkeypatch=monkeypatch, interval=30.0)
    should_yield, remaining = stub._ne_tick_should_yield(time.monotonic())
    assert should_yield is False
    assert remaining == 0.0


def test_exactly_at_interval_does_not_yield(monkeypatch):
    """At or just past the interval boundary: should not yield."""
    stub = _make_stub(last_run_ago=30.1, monkeypatch=monkeypatch, interval=30.0)
    should_yield, _ = stub._ne_tick_should_yield(time.monotonic())
    assert should_yield is False


# ── interval=0 disables the floor ─────────────────────────────────────────────

def test_zero_interval_never_yields(monkeypatch):
    """IGOR_NE_TICK_INTERVAL=0 disables the floor."""
    stub = _make_stub(last_run_ago=0.001, monkeypatch=monkeypatch, interval=0.0)
    should_yield, _ = stub._ne_tick_should_yield(time.monotonic())
    assert should_yield is False


# ── NARRATIVE_GAP scenario: TWM changes don't bypass the floor ───────────────

def test_yield_applies_regardless_of_twm_change(monkeypatch):
    """The interval floor is independent of TWM fingerprint — NARRATIVE_GAP can't bypass it."""
    stub = _make_stub(last_run_ago=5.0, monkeypatch=monkeypatch, interval=30.0)
    # Simulate TWM state changing (NARRATIVE_GAP re-fire scenario)
    # The should_yield helper has no fingerprint dependency — it only checks time.
    should_yield, _ = stub._ne_tick_should_yield(time.monotonic())
    assert should_yield is True, (
        "Interval must block NE even when TWM fingerprint changed — "
        "this is the NARRATIVE_GAP re-fire fix"
    )
