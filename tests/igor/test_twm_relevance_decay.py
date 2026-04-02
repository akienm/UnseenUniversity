"""
test_twm_relevance_decay.py — Tests for T-twm-relevance-decay.

Goal-relevance-weighted TWM TTL shortening.

Methods under test:
  cortex.twm_relevance_score(entry_content, goal_text) -> float
  cortex.twm_apply_goal_decay() -> int

All tests mock cortex._local_conn() — no live DB required.
"""

import os
import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call

import pytest

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_cortex():
    """
    Return a minimal stub that carries the TWM relevance-decay methods under test.

    Binds the unbound Cortex methods onto a plain stub so no DB is initialised
    and no internal __init__ patching is required.
    """
    import types
    from wild_igor.igor.memory.cortex import Cortex

    class _CortexStub:
        _instance_id = "test-instance"

    stub = _CortexStub()
    stub.twm_relevance_score = types.MethodType(Cortex.twm_relevance_score, stub)
    stub.twm_apply_goal_decay = types.MethodType(Cortex.twm_apply_goal_decay, stub)
    # Per-test: replace _local_conn and twm_get_active_goal with mocks
    stub._local_conn = MagicMock()
    stub.twm_get_active_goal = MagicMock(return_value=None)
    return stub


# ── twm_relevance_score ───────────────────────────────────────────────────────


class TestTwmRelevanceScore:
    def test_relevance_score_exact_match(self):
        """Same words in goal and entry → score 1.0."""
        cortex = make_cortex()
        score = cortex.twm_relevance_score(
            "read the book about python", "read the book about python"
        )
        assert score == 1.0

    def test_relevance_score_no_overlap(self):
        """Completely different words → score 0.0."""
        cortex = make_cortex()
        score = cortex.twm_relevance_score(
            "banana elephant purple", "keyboard monitor cable"
        )
        assert score == 0.0

    def test_relevance_score_partial_overlap(self):
        """Some shared tokens → score between 0.0 and 1.0."""
        cortex = make_cortex()
        # entry: {"read", "book", "python", "tutorial"} — 4 tokens
        # goal:  {"read", "python"} — 2 shared
        # overlap / len(entry_tokens) = 2/4 = 0.5
        score = cortex.twm_relevance_score(
            "read book python tutorial", "read python algebra"
        )
        assert 0.0 < score < 1.0
        assert score == pytest.approx(0.5)

    def test_relevance_score_neutral_on_no_goal(self):
        """None goal → 0.5 (neutral — no decay penalty)."""
        cortex = make_cortex()
        assert cortex.twm_relevance_score("some content here", None) == 0.5

    def test_relevance_score_neutral_on_empty_goal(self):
        """Empty string goal → 0.5."""
        cortex = make_cortex()
        assert cortex.twm_relevance_score("some content here", "") == 0.5

    def test_relevance_score_neutral_on_empty_entry(self):
        """Empty entry → 0.5."""
        cortex = make_cortex()
        assert cortex.twm_relevance_score("", "some goal") == 0.5

    def test_relevance_score_capped_at_one(self):
        """Score never exceeds 1.0."""
        cortex = make_cortex()
        score = cortex.twm_relevance_score("a b", "a b c d e f")
        assert score <= 1.0

    def test_relevance_score_case_insensitive(self):
        """Token comparison is case-insensitive."""
        cortex = make_cortex()
        score = cortex.twm_relevance_score("Read The Book", "read the book")
        assert score == 1.0


# ── twm_apply_goal_decay ──────────────────────────────────────────────────────


def _make_row(obs_id, content, expires_at, category="general"):
    """Return a dict that supports row['key'] access like sqlite3.Row."""
    return {
        "id": obs_id,
        "content_csb": content,
        "expires_at": expires_at,
        "category": category,
    }


class TestTwmApplyGoalDecay:
    # ── common setup ──────────────────────────────────────────────────────────

    def _setup_conn(self, cortex, rows, goal_text):
        """
        Wire up _local_conn context manager and twm_get_active_goal.
        Returns the mock connection so tests can verify execute() calls.
        """
        mock_conn = MagicMock()
        # SELECT returns rows; UPDATE returns a cursor with rowcount
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = rows
        mock_conn.execute.return_value = mock_cursor

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_conn)
        ctx.__exit__ = MagicMock(return_value=False)
        cortex._local_conn = MagicMock(return_value=ctx)

        cortex.twm_get_active_goal = MagicMock(return_value=goal_text)
        return mock_conn

    # ── no goal ───────────────────────────────────────────────────────────────

    def test_no_goal_returns_zero(self):
        """When no ACTIVE_GOAL is set, returns 0 with no DB writes."""
        cortex = make_cortex()
        cortex.twm_get_active_goal = MagicMock(return_value=None)
        cortex._local_conn = MagicMock()

        result = cortex.twm_apply_goal_decay()

        assert result == 0
        cortex._local_conn.assert_not_called()

    # ── low-relevance entries expire sooner ───────────────────────────────────

    def test_shortens_low_relevance_entry(self):
        """Entry with no goal overlap gets expires_at shortened."""
        cortex = make_cortex()

        now = datetime.now()
        future = (now + timedelta(seconds=600)).isoformat()

        # Entry content shares NO words with the goal
        rows = [_make_row(42, "banana elephant purple", future)]
        mock_conn = self._setup_conn(cortex, rows, goal_text="write python code")

        with patch(
            "wild_igor.igor.memory.cortex.os.getenv",
            side_effect=lambda k, d=None: (
                "2.0" if k == "IGOR_TWM_GOAL_DECAY_PENALTY" else os.getenv(k, d)
            ),
        ):
            result = cortex.twm_apply_goal_decay()

        assert result == 1
        # Verify UPDATE was called (shortened expires_at)
        update_calls = [
            c for c in mock_conn.execute.call_args_list if "UPDATE" in str(c)
        ]
        assert len(update_calls) == 1

    # ── high-relevance entries unchanged ─────────────────────────────────────

    def test_preserves_high_relevance_entry(self):
        """Entry fully matching the goal should NOT be updated."""
        cortex = make_cortex()

        now = datetime.now()
        future = (now + timedelta(seconds=600)).isoformat()

        # Entry content exactly matches goal tokens
        rows = [_make_row(7, "write python code", future)]
        mock_conn = self._setup_conn(cortex, rows, goal_text="write python code")

        result = cortex.twm_apply_goal_decay()

        assert result == 0
        update_calls = [
            c for c in mock_conn.execute.call_args_list if "UPDATE" in str(c)
        ]
        assert len(update_calls) == 0

    # ── ACTIVE_GOAL entries are exempt ────────────────────────────────────────

    def test_skips_active_goal_entry(self):
        """
        The SQL query filters category != 'active_goal', so ACTIVE_GOAL entries
        never appear in the result set. Verify no UPDATE is issued for them.
        """
        cortex = make_cortex()

        now = datetime.now()
        future = (now + timedelta(seconds=300)).isoformat()

        # Simulate: DB returns zero rows (because active_goal was filtered out by SQL)
        rows = []
        mock_conn = self._setup_conn(cortex, rows, goal_text="some goal")

        result = cortex.twm_apply_goal_decay()

        assert result == 0
        update_calls = [
            c for c in mock_conn.execute.call_args_list if "UPDATE" in str(c)
        ]
        assert len(update_calls) == 0

    # ── entries with no TTL are skipped ──────────────────────────────────────

    def test_skips_entry_with_no_ttl(self):
        """Entry with expires_at=None has no TTL to shorten — skip silently."""
        cortex = make_cortex()

        rows = [_make_row(99, "banana", None)]
        mock_conn = self._setup_conn(cortex, rows, goal_text="python code")

        result = cortex.twm_apply_goal_decay()

        assert result == 0

    # ── env var configures penalty ────────────────────────────────────────────

    def test_goal_decay_penalty_env_var(self):
        """IGOR_TWM_GOAL_DECAY_PENALTY env var is read and applied."""
        cortex = make_cortex()

        now = datetime.now()
        future = (now + timedelta(seconds=600)).isoformat()

        rows_low = [_make_row(1, "banana elephant", future)]

        # penalty=1.0 → decay_multiplier for relevance=0.0: 1+(1-0)*1=2 → new_remaining=300
        mock_conn_low = self._setup_conn(
            cortex, rows_low, goal_text="python write code"
        )

        with patch.dict(os.environ, {"IGOR_TWM_GOAL_DECAY_PENALTY": "1.0"}):
            result_low = cortex.twm_apply_goal_decay()

        assert result_low == 1  # still shortened (multiplier=2, so 600→300)

        rows_high = [_make_row(2, "banana elephant", future)]
        mock_conn_high = self._setup_conn(
            cortex, rows_high, goal_text="python write code"
        )

        with patch.dict(os.environ, {"IGOR_TWM_GOAL_DECAY_PENALTY": "5.0"}):
            result_high = cortex.twm_apply_goal_decay()

        assert result_high == 1  # also shortened, but more aggressively

    def test_goal_decay_penalty_clamped(self):
        """IGOR_TWM_GOAL_DECAY_PENALTY is clamped to 1.0–5.0."""
        cortex = make_cortex()

        now = datetime.now()
        future = (now + timedelta(seconds=600)).isoformat()

        rows = [_make_row(1, "banana", future)]
        self._setup_conn(cortex, rows, goal_text="python code")

        # Penalty > 5.0 should be clamped to 5.0 — method should not raise
        with patch.dict(os.environ, {"IGOR_TWM_GOAL_DECAY_PENALTY": "99.0"}):
            result = cortex.twm_apply_goal_decay()
        # Should complete without error; updated count is 1 (entry was shortened)
        assert result == 1

    # ── only shortens, never extends ─────────────────────────────────────────

    def test_never_extends_ttl(self):
        """Goal decay only shortens expires_at — never extends."""
        cortex = make_cortex()

        now = datetime.now()
        # Entry expires very soon — only 5 seconds
        near_future = (now + timedelta(seconds=5)).isoformat()

        rows = [_make_row(3, "banana elephant", near_future)]
        mock_conn = self._setup_conn(cortex, rows, goal_text="python code write")

        result = cortex.twm_apply_goal_decay()

        # new_expires must be < near_future (or equal, but never greater)
        update_calls = [
            c for c in mock_conn.execute.call_args_list if "UPDATE" in str(c)
        ]
        if update_calls:
            # The new expires_at arg (first positional arg to UPDATE) must be earlier
            new_expires_str = update_calls[0][0][1][0]
            assert new_expires_str <= near_future


# ── Integration: emit_channels goal shift triggers decay ─────────────────────


class TestEmitChannelsGoalDecayIntegration:
    """Verify CognitiveMilieuChannel calls twm_apply_goal_decay on ACTIVE_GOAL."""

    def test_active_goal_push_triggers_goal_decay(self):
        """When ACTIVE_GOAL is pushed, twm_apply_goal_decay should be called."""
        from wild_igor.igor.cognition.emit_channels import CognitiveMilieuChannel

        channel = CognitiveMilieuChannel()
        mock_cortex = MagicMock()
        mock_cortex.twm_apply_goal_decay.return_value = 3
        basket = {"_cortex": mock_cortex, "_salience": 0.7}

        channel.write("ACTIVE_GOAL", "finish the python refactor", basket)

        mock_cortex.twm_evict_category.assert_called_once_with("active_goal")
        mock_cortex.twm_push.assert_called_once()
        mock_cortex.twm_apply_goal_decay.assert_called_once()

    def test_non_goal_push_does_not_trigger_goal_decay(self):
        """Non-ACTIVE_GOAL pushes should NOT call twm_apply_goal_decay."""
        from wild_igor.igor.cognition.emit_channels import CognitiveMilieuChannel

        channel = CognitiveMilieuChannel()
        mock_cortex = MagicMock()
        basket = {"_cortex": mock_cortex}

        channel.write("SOME_OTHER_KEY", "some value", basket)

        mock_cortex.twm_apply_goal_decay.assert_not_called()
