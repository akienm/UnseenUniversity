"""
test_twm_salience_decay.py — T-twm-salience-time-decay.

Pairs with T-twm-leap-on-lever (decay = "big topics fade", leap = "latent
items leap when a lever appears"). This file covers the decay half: the
SELF_EVICTED bug from 2026-04-23 happened because TWM rows kept their
initial salience forever, so old goal_adopt rows at 0.85+ blocked every
new push at <0.85. After this fix, effective salience used at eviction
ranking is `stored * 0.5^(age/halflife)`.

Tests cover:
  - _twm_decay_factor (pure function; halflife tunable via env)
  - _twm_pick_eviction_victims (pure ranking: integrated DESC, eff_sal ASC, id ASC)
  - The integration case from the ticket: old high-salience row loses to
    fresh lower-salience row at eviction time.
"""

import os
from datetime import datetime, timedelta

import pytest

from unseen_university.devices.igor.memory.cortex import (
    _twm_decay_factor,
    _twm_effective_salience,
    _twm_pick_eviction_victims,
)

# ── _twm_decay_factor ────────────────────────────────────────────────────────


class TestDecayFactor:
    def test_age_zero_returns_one(self, monkeypatch):
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "7200")
        assert _twm_decay_factor(0) == 1.0

    def test_age_at_halflife_returns_half(self, monkeypatch):
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "7200")
        assert _twm_decay_factor(7200) == pytest.approx(0.5)

    def test_age_at_two_halflives_returns_quarter(self, monkeypatch):
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "7200")
        assert _twm_decay_factor(14400) == pytest.approx(0.25)

    def test_monotonic_non_increasing(self, monkeypatch):
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "3600")
        ages = [0, 60, 600, 1800, 3600, 7200, 18000]
        factors = [_twm_decay_factor(a) for a in ages]
        for prev, curr in zip(factors, factors[1:]):
            assert curr <= prev

    def test_negative_age_returns_one(self, monkeypatch):
        # Defensive: clock skew etc.
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "7200")
        assert _twm_decay_factor(-100) == 1.0

    def test_disabled_when_halflife_zero(self, monkeypatch):
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "0")
        assert _twm_decay_factor(99999) == 1.0

    def test_env_var_tunable(self, monkeypatch):
        # Same age, different halflife → different decay
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "3600")
        fast = _twm_decay_factor(3600)  # 1 halflife → 0.5
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "36000")
        slow = _twm_decay_factor(3600)  # 0.1 halflife → ~0.93
        assert fast == pytest.approx(0.5)
        assert slow > 0.9
        assert fast < slow


# ── _twm_effective_salience ──────────────────────────────────────────────────


def _row(id_, salience, integrated, timestamp_iso):
    return {
        "id": id_,
        "salience": salience,
        "integrated": integrated,
        "timestamp": timestamp_iso,
    }


class TestEffectiveSalience:
    def test_fresh_row_salience_unchanged(self, monkeypatch):
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "7200")
        now = datetime(2026, 4, 23, 12, 0, 0)
        r = _row(1, 0.85, 0, now.isoformat())
        assert _twm_effective_salience(r, now) == pytest.approx(0.85)

    def test_old_row_salience_decays(self, monkeypatch):
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "7200")
        now = datetime(2026, 4, 23, 12, 0, 0)
        old = (now - timedelta(seconds=7200)).isoformat()
        r = _row(1, 0.85, 0, old)
        assert _twm_effective_salience(r, now) == pytest.approx(0.425)

    def test_unparseable_timestamp_falls_back_to_no_decay(self, monkeypatch):
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "7200")
        now = datetime(2026, 4, 23, 12, 0, 0)
        r = _row(1, 0.85, 0, "not-a-timestamp")
        assert _twm_effective_salience(r, now) == pytest.approx(0.85)

    def test_null_salience_treated_as_zero(self, monkeypatch):
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "7200")
        now = datetime(2026, 4, 23, 12, 0, 0)
        r = _row(1, None, 0, now.isoformat())
        assert _twm_effective_salience(r, now) == 0.0


# ── _twm_pick_eviction_victims ───────────────────────────────────────────────


class TestEvictionRanking:
    def test_integrated_dies_before_unintegrated(self, monkeypatch):
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "7200")
        now = datetime(2026, 4, 23, 12, 0, 0)
        rows = [
            _row(1, 0.9, 1, now.isoformat()),  # integrated, high salience
            _row(2, 0.1, 0, now.isoformat()),  # unintegrated, low salience
        ]
        victims = _twm_pick_eviction_victims(rows, overflow=1, now=now)
        # integrated wins eviction even at higher salience
        assert [v["id"] for v in victims] == [1]

    def test_low_salience_dies_first_among_unintegrated(self, monkeypatch):
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "7200")
        now = datetime(2026, 4, 23, 12, 0, 0)
        rows = [
            _row(1, 0.9, 0, now.isoformat()),
            _row(2, 0.3, 0, now.isoformat()),
            _row(3, 0.5, 0, now.isoformat()),
        ]
        victims = _twm_pick_eviction_victims(rows, overflow=1, now=now)
        assert [v["id"] for v in victims] == [2]  # 0.3 is lowest

    def test_id_breaks_tie_among_equal_salience(self, monkeypatch):
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "7200")
        now = datetime(2026, 4, 23, 12, 0, 0)
        rows = [
            _row(5, 0.5, 0, now.isoformat()),
            _row(2, 0.5, 0, now.isoformat()),
            _row(7, 0.5, 0, now.isoformat()),
        ]
        victims = _twm_pick_eviction_victims(rows, overflow=2, now=now)
        # Lower id (older) dies first
        assert [v["id"] for v in victims] == [2, 5]

    def test_ticket_scenario_old_high_loses_to_fresh_low(self, monkeypatch):
        """The bug: old goal_adopt at 0.85 (hours old) blocked fresh push at 0.6.
        After decay: old row's effective = 0.85 * 0.5^(7200/7200) = 0.425.
        Fresh push at 0.6 has effective = 0.6 → fresh wins, old gets evicted."""
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "7200")
        now = datetime(2026, 4, 23, 12, 0, 0)
        old_ts = (now - timedelta(seconds=7200)).isoformat()  # one halflife old
        rows = [
            _row(1, 0.85, 0, old_ts),  # old high — eff ~0.425
            _row(2, 0.6, 0, now.isoformat()),  # fresh lower — eff 0.6
        ]
        victims = _twm_pick_eviction_victims(rows, overflow=1, now=now)
        # The old high-salience row gets evicted; fresh new row survives.
        # Pre-fix this would have been [2] (raw salience 0.6 < 0.85).
        assert [v["id"] for v in victims] == [1]

    def test_decay_disabled_falls_back_to_raw_salience(self, monkeypatch):
        """Halflife=0 disables decay. Old high-salience wins again — useful for
        proving the regression: with decay off, we get the old (broken) behavior."""
        monkeypatch.setenv("IGOR_TWM_SALIENCE_HALFLIFE_SEC", "0")
        now = datetime(2026, 4, 23, 12, 0, 0)
        old_ts = (now - timedelta(seconds=86400)).isoformat()  # a full day old
        rows = [
            _row(1, 0.85, 0, old_ts),
            _row(2, 0.6, 0, now.isoformat()),
        ]
        victims = _twm_pick_eviction_victims(rows, overflow=1, now=now)
        # With decay off, the fresh low-salience row gets evicted (the bug shape).
        assert [v["id"] for v in victims] == [2]
