"""
test_boredom_goal_coupling.py — T-boredom-goal-coupling (#426)

Unit tests for the goal-surfacing extension of BoredomSource. The
existing boredom detection + cooldown + milieu nudge behavior is
untouched and covered by other tests — this file focuses on the new
_surface_active_goals() helper.

Under CP1 these tests verify:
  - Goals are surfaced as CANDIDATE attractors, not commitments
    (cp1_provisional=True in metadata)
  - Only active goal facia are considered (dormant/archived filtered)
  - Only goal-flavored relationship_types are considered
  - Ranking by weight * recency * progress-gap produces stable order
  - Empty goal set = empty pushed list (no-op, not crash)
  - Import failure / fetch failure degrades gracefully
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition.push_sources import BoredomSource  # noqa: E402


def _now_iso(offset_days: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=offset_days)).isoformat()


def _goal_facia(
    facia_id: str,
    display_name: str,
    relationship_type: str = "goal_strategic",
    status: str = "active",
    weight: float = 1.0,
    age_days: float = 0.0,
    progress: float = 0.0,
    desired_future_state: str = "example future state",
) -> dict:
    return {
        "id": facia_id,
        "narrative": f"goal: {display_name}",
        "metadata": {
            "node_kind": "facia",
            "facia_role": "persistent_relationship",
            "relationship_type": relationship_type,
            "display_name": display_name,
            "status": status,
            "cumulative_investment_weight": weight,
            "last_activity_ts": _now_iso(age_days),
            "progress": progress,
            "desired_future_state": desired_future_state,
        },
    }


def _mock_cortex_capturing_pushes() -> MagicMock:
    """Mock cortex that records twm_push calls and returns sequential obs ids."""
    cortex = MagicMock()
    counter = {"i": 0}

    def _push(**kwargs):
        counter["i"] += 1
        return counter["i"]

    cortex.twm_push.side_effect = _push
    return cortex


# ── Empty / trivial cases ────────────────────────────────────────────────────


def test_surface_empty_when_no_goals():
    cortex = _mock_cortex_capturing_pushes()
    src = BoredomSource()
    with patch("wild_igor.igor.tools.goal_graph._fetch_goal_facia", return_value=[]):
        out = src._surface_active_goals(cortex)
    assert out == []
    assert not cortex.twm_push.called


def test_surface_empty_when_all_dormant():
    cortex = _mock_cortex_capturing_pushes()
    src = BoredomSource()
    goals = [
        _goal_facia("PR_GOAL_1", "dormant one", status="dormant"),
        _goal_facia("PR_GOAL_2", "archived one", status="archived"),
    ]
    with patch("wild_igor.igor.tools.goal_graph._fetch_goal_facia", return_value=goals):
        out = src._surface_active_goals(cortex)
    assert out == []
    assert not cortex.twm_push.called


# ── Happy path ───────────────────────────────────────────────────────────────


def test_surface_pushes_top_goal():
    cortex = _mock_cortex_capturing_pushes()
    src = BoredomSource()
    goals = [
        _goal_facia("PR_GOAL_ASP", "help the world suck less", weight=2.0),
    ]
    with patch("wild_igor.igor.tools.goal_graph._fetch_goal_facia", return_value=goals):
        out = src._surface_active_goals(cortex)
    assert len(out) == 1
    assert cortex.twm_push.call_count == 1

    call = cortex.twm_push.call_args
    kwargs = call.kwargs
    assert kwargs["source"] == "boredom_detector"
    assert "ACTIVE_GOAL_SURFACED" in kwargs["content_csb"]
    assert "PR_GOAL_ASP" in kwargs["content_csb"]

    meta = kwargs["metadata"]
    assert meta["type"] == "active_goal_surfaced"
    assert meta["via"] == "boredom"
    assert meta["facia_id"] == "PR_GOAL_ASP"
    assert meta["display_name"] == "help the world suck less"
    assert meta["cp1_provisional"] is True


def test_surface_only_one_goal_even_when_many_candidates():
    """We surface ONE (not a flood) so substrate can re-query if needed."""
    cortex = _mock_cortex_capturing_pushes()
    src = BoredomSource()
    goals = [
        _goal_facia(f"PR_GOAL_{i}", f"goal {i}", weight=1.0 + i * 0.1) for i in range(5)
    ]
    with patch("wild_igor.igor.tools.goal_graph._fetch_goal_facia", return_value=goals):
        out = src._surface_active_goals(cortex)
    assert len(out) == 1
    assert cortex.twm_push.call_count == 1


# ── Ranking by weight * recency * progress-gap ───────────────────────────────


def test_ranking_prefers_higher_weight():
    cortex = _mock_cortex_capturing_pushes()
    src = BoredomSource()
    goals = [
        _goal_facia("PR_GOAL_LOW", "low weight", weight=0.5),
        _goal_facia("PR_GOAL_HIGH", "high weight", weight=2.0),
    ]
    with patch("wild_igor.igor.tools.goal_graph._fetch_goal_facia", return_value=goals):
        src._surface_active_goals(cortex)
    meta = cortex.twm_push.call_args.kwargs["metadata"]
    assert meta["facia_id"] == "PR_GOAL_HIGH"


def test_ranking_prefers_recent_over_stale():
    cortex = _mock_cortex_capturing_pushes()
    src = BoredomSource()
    goals = [
        _goal_facia("PR_GOAL_STALE", "stale", weight=1.0, age_days=60),
        _goal_facia("PR_GOAL_FRESH", "fresh", weight=1.0, age_days=0),
    ]
    with patch("wild_igor.igor.tools.goal_graph._fetch_goal_facia", return_value=goals):
        src._surface_active_goals(cortex)
    meta = cortex.twm_push.call_args.kwargs["metadata"]
    assert meta["facia_id"] == "PR_GOAL_FRESH"


def test_ranking_progress_gap_boost():
    """Fresh (progress=0.0) outscores near-complete (progress=0.9) at equal weight + recency."""
    cortex = _mock_cortex_capturing_pushes()
    src = BoredomSource()
    goals = [
        _goal_facia("PR_GOAL_DONE", "almost done", weight=1.0, progress=0.9),
        _goal_facia("PR_GOAL_OPEN", "untouched", weight=1.0, progress=0.0),
    ]
    with patch("wild_igor.igor.tools.goal_graph._fetch_goal_facia", return_value=goals):
        src._surface_active_goals(cortex)
    meta = cortex.twm_push.call_args.kwargs["metadata"]
    assert meta["facia_id"] == "PR_GOAL_OPEN"


# ── Graceful degradation ─────────────────────────────────────────────────────


def test_surface_degrades_on_fetch_failure():
    cortex = _mock_cortex_capturing_pushes()
    src = BoredomSource()
    with patch(
        "wild_igor.igor.tools.goal_graph._fetch_goal_facia",
        side_effect=RuntimeError("db down"),
    ):
        out = src._surface_active_goals(cortex)
    assert out == []
    assert not cortex.twm_push.called


def test_surface_degrades_on_twm_push_failure():
    cortex = MagicMock()
    cortex.twm_push.side_effect = RuntimeError("twm unavailable")
    src = BoredomSource()
    goals = [_goal_facia("PR_GOAL_X", "x")]
    with patch("wild_igor.igor.tools.goal_graph._fetch_goal_facia", return_value=goals):
        out = src._surface_active_goals(cortex)
    # twm_push was called once but returned an error — helper swallowed it
    assert out == []
    assert cortex.twm_push.call_count == 1


def test_surface_handles_missing_metadata_fields():
    """Bare-minimum metadata (no weight, no progress, no recency) should still
    rank without crashing."""
    cortex = _mock_cortex_capturing_pushes()
    src = BoredomSource()
    goals = [
        {
            "id": "PR_GOAL_BARE",
            "narrative": "bare",
            "metadata": {
                "facia_role": "persistent_relationship",
                "relationship_type": "goal_strategic",
                "status": "active",
                "display_name": "bare goal",
                # no weight, no progress, no last_activity_ts
            },
        }
    ]
    with patch("wild_igor.igor.tools.goal_graph._fetch_goal_facia", return_value=goals):
        out = src._surface_active_goals(cortex)
    # Zero weight → score 0, but still surfaces as the only active goal
    assert len(out) == 1
    meta = cortex.twm_push.call_args.kwargs["metadata"]
    assert meta["facia_id"] == "PR_GOAL_BARE"


# ── CP consistency (cp1_provisional flag survives to TWM metadata) ───────────


def test_surfaced_goal_carries_cp1_provisional_flag():
    cortex = _mock_cortex_capturing_pushes()
    src = BoredomSource()
    goals = [_goal_facia("PR_GOAL_CP", "cp test")]
    with patch("wild_igor.igor.tools.goal_graph._fetch_goal_facia", return_value=goals):
        src._surface_active_goals(cortex)
    meta = cortex.twm_push.call_args.kwargs["metadata"]
    assert meta["cp1_provisional"] is True
