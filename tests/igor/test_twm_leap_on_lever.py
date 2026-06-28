"""
test_twm_leap_on_lever.py — T-twm-leap-on-lever.

Pairs with T-twm-salience-time-decay — decay fades big topics so latent
slots open; leap lifts latent items when an accidental lever arrives.

Per memory/project_salience_decay_and_lever.md, four load-bearing properties:
  1. Levers are accidental, not searched-for (Christmas-present-in-June).
  2. One lever can resolve multiple latent items simultaneously.
  3. Recognition is instant (single pass, not deliberative).
  4. Association is semantic (graph-spread), not textual (keyword overlap).

Tests exercise leap_sweep against mock conn + mock word_graph, with the
real `tokenize` function doing the text → tokens work so we catch actual
token-set mismatches (not mock-shaped ones).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from unseen_university.devices.igor.memory.twm_leap import leap_sweep


class _FakeConn:
    """Minimal conn stub — captures SELECT rows + UPDATE calls."""

    def __init__(self, rows):
        self._rows = rows
        self.updates: list[tuple[float, int]] = []  # (new_salience, row_id)

    def execute(self, sql, params=()):
        if sql.startswith("SELECT"):
            cur = MagicMock()
            cur.fetchall.return_value = self._rows
            return cur
        if sql.startswith("UPDATE"):
            new_sal, row_id = params
            self.updates.append((new_sal, row_id))
            # Reflect back into _rows so subsequent SELECTs see updated values
            for r in self._rows:
                if r["id"] == row_id:
                    r["salience"] = new_sal
            return MagicMock()
        raise AssertionError(f"unexpected SQL: {sql!r}")


def _row(id_, content, salience, integrated=0):
    return {
        "id": id_,
        "content_csb": content,
        "salience": salience,
        "integrated": integrated,
    }


def _fake_word_graph(activation_map: dict[str, float]):
    """Returns a mock WordGraph whose spread_from_words yields activation_map."""
    wg = MagicMock()
    wg.spread_from_words.return_value = activation_map
    return wg


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    """Clean env for each test so defaults apply unless a test overrides them."""
    for k in (
        "IGOR_TWM_LEAP_ENABLED",
        "IGOR_TWM_LATENT_FLOOR",
        "IGOR_TWM_LEAP_THRESHOLD",
        "IGOR_TWM_LEAP_BOOST",
    ):
        monkeypatch.delenv(k, raising=False)


# ── No-op guards ──────────────────────────────────────────────────────────────


class TestNoOpGuards:
    def test_returns_empty_when_disabled(self, monkeypatch):
        monkeypatch.setenv("IGOR_TWM_LEAP_ENABLED", "0")
        conn = _FakeConn([_row(1, "leah christmas", 0.2)])
        wg = _fake_word_graph({"leah": 1.0})
        assert leap_sweep(conn, 99, "perfect gift for leah", wg) == []
        assert conn.updates == []

    def test_returns_empty_when_no_word_graph(self):
        conn = _FakeConn([_row(1, "leah christmas", 0.2)])
        assert leap_sweep(conn, 99, "perfect gift for leah", None) == []
        assert conn.updates == []

    def test_returns_empty_when_new_content_empty(self):
        conn = _FakeConn([_row(1, "leah christmas", 0.2)])
        wg = _fake_word_graph({"leah": 1.0})
        assert leap_sweep(conn, 99, "", wg) == []
        assert conn.updates == []

    def test_returns_empty_when_spread_yields_nothing(self):
        conn = _FakeConn([_row(1, "leah christmas", 0.2)])
        wg = _fake_word_graph({})
        assert leap_sweep(conn, 99, "perfect gift for leah", wg) == []
        assert conn.updates == []

    def test_spread_exception_is_caught_not_raised(self):
        conn = _FakeConn([_row(1, "leah christmas", 0.2)])
        wg = MagicMock()
        wg.spread_from_words.side_effect = RuntimeError("graph blew up")
        assert leap_sweep(conn, 99, "perfect gift for leah", wg) == []
        assert conn.updates == []


# ── The ticket's core scenarios ──────────────────────────────────────────────


class TestPositiveLeap:
    def test_direct_token_hit_triggers_leap(self, monkeypatch):
        """Lever shares a content token with a latent row. Row leaps."""
        monkeypatch.setenv("IGOR_TWM_LEAP_THRESHOLD", "0.5")
        monkeypatch.setenv("IGOR_TWM_LEAP_BOOST", "0.3")
        conn = _FakeConn([_row(1, "what to get leah for christmas", salience=0.2)])
        # Simulate spread: leah is activated at 1.0 (it's a seed).
        wg = _fake_word_graph({"leah": 1.0, "gift": 0.4, "christmas": 0.3})

        leaps = leap_sweep(conn, 99, "perfect thing for leah in shop window", wg)

        assert len(leaps) == 1
        row_id, old_sal, new_sal = leaps[0]
        assert row_id == 1
        assert old_sal == pytest.approx(0.2)
        assert new_sal == pytest.approx(0.5)
        assert conn.updates == [(pytest.approx(0.5), 1)]

    def test_one_lever_resolves_multiple_latent_rows(self, monkeypatch):
        """Load-bearing property #2: one lever boosts every matching row."""
        monkeypatch.setenv("IGOR_TWM_LEAP_THRESHOLD", "0.5")
        conn = _FakeConn(
            [
                _row(1, "get leah a present", salience=0.2),
                _row(2, "make leah happy", salience=0.15),
                _row(3, "unrelated plumbing fix", salience=0.2),
            ]
        )
        wg = _fake_word_graph({"leah": 1.0, "present": 0.5})

        leaps = leap_sweep(conn, 99, "saw perfect thing for leah", wg)

        boosted_ids = {row_id for row_id, _, _ in leaps}
        assert boosted_ids == {1, 2}  # both leah rows; plumbing untouched
        assert len(conn.updates) == 2

    def test_semantic_not_textual_match(self, monkeypatch):
        """Load-bearing property #4: the lever can share no keywords with
        the latent row — what matters is graph-spread overlap. Here the
        lever text has no overlap with 'christmas', but 'christmas' is
        activated via the graph (hop neighbor of 'gift')."""
        monkeypatch.setenv("IGOR_TWM_LEAP_THRESHOLD", "0.5")
        conn = _FakeConn([_row(1, "what to get leah for christmas", salience=0.2)])
        # Lever content contains none of {leah, christmas}. But spread gave
        # christmas = 0.6, leah = 0.4 (semantic neighbors of the lever tokens).
        wg = _fake_word_graph({"christmas": 0.6, "leah": 0.4})

        leaps = leap_sweep(conn, 99, "december shopping window display", wg)

        assert len(leaps) == 1  # still boosted via semantic spread
        assert leaps[0][0] == 1

    def test_boost_capped_at_one(self, monkeypatch):
        """Salience can't exceed 1.0 even if multiple overlaps would push past."""
        monkeypatch.setenv("IGOR_TWM_LEAP_THRESHOLD", "0.5")
        monkeypatch.setenv("IGOR_TWM_LEAP_BOOST", "0.5")
        conn = _FakeConn([_row(1, "leah christmas", salience=0.85)])
        wg = _fake_word_graph({"leah": 1.0, "christmas": 1.0})

        leaps = leap_sweep(conn, 99, "leah", wg)
        _, old_sal, new_sal = leaps[0]
        assert new_sal == pytest.approx(1.0)
        assert old_sal == pytest.approx(0.85)


# ── Negative cases ───────────────────────────────────────────────────────────


class TestNoLeap:
    def test_unrelated_observation_boosts_nothing(self, monkeypatch):
        monkeypatch.setenv("IGOR_TWM_LEAP_THRESHOLD", "0.5")
        conn = _FakeConn([_row(1, "leah christmas", salience=0.2)])
        wg = _fake_word_graph({"kernel": 1.0, "bug": 0.5})  # no overlap

        leaps = leap_sweep(conn, 99, "kernel panic in bug reproducer", wg)

        assert leaps == []
        assert conn.updates == []

    def test_above_floor_rows_are_not_candidates(self, monkeypatch):
        """Latent floor excludes rows that are already foreground."""
        monkeypatch.setenv("IGOR_TWM_LATENT_FLOOR", "0.4")
        monkeypatch.setenv("IGOR_TWM_LEAP_THRESHOLD", "0.5")
        # FakeConn's SELECT returns all rows; the real SQL would filter by
        # salience < floor. We emulate that by pre-filtering here.
        conn = _FakeConn([])  # no rows below floor
        wg = _fake_word_graph({"leah": 1.0})

        leaps = leap_sweep(conn, 99, "leah", wg)

        assert leaps == []

    def test_self_row_excluded_even_if_it_matches(self, monkeypatch):
        """The new obs itself must not be its own leap target. Real SQL
        excludes via `id != ?`. The sweep builds the SELECT with that filter;
        we just verify the parameter gets passed correctly by checking the
        mock was called with the new_obs_id."""
        monkeypatch.setenv("IGOR_TWM_LEAP_THRESHOLD", "0.5")
        conn = MagicMock()
        conn.execute.return_value.fetchall.return_value = []
        wg = _fake_word_graph({"leah": 1.0})

        leap_sweep(conn, 99, "leah", wg)

        call_args = conn.execute.call_args
        assert call_args is not None
        sql, params = call_args[0]
        assert "id != %s" in sql
        assert 99 in params  # new_obs_id passed as SQL parameter

    def test_threshold_respected(self, monkeypatch):
        """Overlap below threshold = no leap."""
        monkeypatch.setenv("IGOR_TWM_LEAP_THRESHOLD", "2.0")  # hard to clear
        conn = _FakeConn([_row(1, "leah christmas", salience=0.2)])
        wg = _fake_word_graph({"leah": 1.0})  # sum = 1.0 < threshold

        leaps = leap_sweep(conn, 99, "leah", wg)
        assert leaps == []
