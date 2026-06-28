"""Tests for devices.igor.cognition.bliss_integrator.

Covers: subscription to Pursuit events, EMA accumulation + decay,
disabled-gate fallback, milieu feedback application.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.cognition import bliss_integrator as bmod  # noqa: E402
from unseen_university.devices.igor.cognition import pursuits as pmod  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch):
    """Clean pursuits registry and bliss integrator between tests; gates on."""
    pmod._registry.clear()
    bmod.reset_for_test()
    monkeypatch.setenv("IGOR_PURSUITS_ENABLED", "true")
    monkeypatch.setenv("IGOR_BLISS_ENABLED", "true")
    yield
    pmod._registry.clear()
    bmod.reset_for_test()


def _complete_a_pursuit(name: str = "test") -> pmod.Pursuit:
    p = pmod.spawn(
        name=name,
        entry_stimulus={},
        goal_facia=lambda s: True,
    )
    p.evaluate_completion({})
    return p


def test_bliss_starts_at_zero():
    b = bmod.get()
    assert b.get_bliss() == 0.0


def test_completion_raises_bliss():
    b = bmod.get()
    _complete_a_pursuit()
    assert b.get_bliss() > 0.0


def test_commitment_alone_does_not_raise_bliss():
    """Only completion events contribute. A spawned-but-unresolved pursuit
    should not nudge bliss."""
    b = bmod.get()
    pmod.spawn(name="open", entry_stimulus={}, goal_facia=lambda s: True)
    assert b.get_bliss() == 0.0


def test_abandonment_does_not_raise_bliss():
    b = bmod.get()
    p = pmod.spawn(
        name="will_abandon",
        entry_stimulus={},
        goal_facia=lambda s: s.get("done") is True,
    )
    p.evaluate_completion({"done": False})
    # Abandonment fires its own dopamine event but bliss only counts completions
    assert b.get_bliss() == 0.0


def test_bliss_saturates_at_max():
    """Repeated completions should be capped at max_bliss."""
    b = bmod.get()
    for i in range(20):
        _complete_a_pursuit(name=f"p{i}")
    assert b.get_bliss() <= b.max_bliss + 1e-9


def test_bliss_decays_over_time():
    """After the window passes, an isolated completion should have decayed."""
    b = bmod.get()
    # Shorten window so the test runs fast
    b.window_secs = 0.05
    _complete_a_pursuit()
    before = b.get_bliss()
    assert before > 0.0
    time.sleep(0.15)  # 3× window → ~5% of original
    after = b.get_bliss()
    assert after < before * 0.25


def test_disabled_gate_returns_zero(monkeypatch):
    monkeypatch.setenv("IGOR_BLISS_ENABLED", "false")
    b = bmod.get()
    _complete_a_pursuit()
    assert b.get_bliss() == 0.0


def test_apply_to_milieu_invokes_hook():
    milieu = MagicMock()
    b = bmod.get()
    _complete_a_pursuit()
    b.apply_to_milieu(milieu)
    assert milieu.ingest_bliss_lift.called
    # Called with a positive level
    args, _ = milieu.ingest_bliss_lift.call_args
    assert args[0] > 0.0


def test_apply_to_milieu_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("IGOR_BLISS_ENABLED", "false")
    milieu = MagicMock()
    b = bmod.get()
    # Event occurred while gate was on? No — both gates off here.
    b.apply_to_milieu(milieu)
    assert not milieu.ingest_bliss_lift.called


def test_apply_to_milieu_noop_when_bliss_zero():
    """Even with gate on, if no completions happened, don't poke milieu."""
    milieu = MagicMock()
    b = bmod.get()
    b.apply_to_milieu(milieu)
    assert not milieu.ingest_bliss_lift.called


def test_apply_to_milieu_tolerates_missing_method():
    """Older milieu without ingest_bliss_lift should not crash."""

    class Bare:
        pass  # no ingest_bliss_lift

    b = bmod.get()
    _complete_a_pursuit()
    # Should log and return, not raise
    b.apply_to_milieu(Bare())


def test_event_count_tracks_completions():
    b = bmod.get()
    assert b.state.event_count == 0
    _complete_a_pursuit()
    _complete_a_pursuit()
    assert b.state.event_count == 2


def test_process_wide_singleton_persists():
    """get() returns the same integrator across calls."""
    first = bmod.get()
    _complete_a_pursuit()
    second = bmod.get()
    assert first is second
    assert second.get_bliss() > 0.0


def test_subscribe_happens_once():
    """Multiple get() calls should not double-subscribe."""
    b = bmod.get()
    b2 = bmod.get()
    _complete_a_pursuit()
    # If double-subscribed, event_count would be 2 for a single completion.
    assert b.state.event_count == 1
    assert b2.state.event_count == 1
