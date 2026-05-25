"""
test_capability_awareness_source.py — T-self-capability-awareness (#431)

Tests for the CapabilityAwarenessSource — surfaces the four uncertainty
strategies + experiment queue depth + tool count to TWM so reasoning
can reach for them.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.cognition.push_sources import (  # noqa: E402
    CapabilityAwarenessSource,
)


def _make_cortex(queue_rows=None):
    cortex = MagicMock()
    conn = MagicMock()
    cortex._db.return_value.__enter__.return_value = conn
    cortex._db.return_value.__exit__.return_value = False
    conn.fetchall.return_value = queue_rows or []
    cortex.twm_push.return_value = 42
    return cortex, conn


# ── Strategies snapshot ──────────────────────────────────────────────────────


def test_snapshot_returns_all_four_strategies():
    src = CapabilityAwarenessSource()
    strategies = src._strategies_snapshot()
    ids = {s["id"] for s in strategies}
    assert ids == {
        "strategy_1_ignore",
        "strategy_2_experiment",
        "strategy_3_ask_test",
        "strategy_4_wait",
    }


def test_passive_strategies_always_runnable():
    src = CapabilityAwarenessSource()
    strategies = {s["id"]: s for s in src._strategies_snapshot()}
    assert strategies["strategy_1_ignore"]["runnable"] is True
    assert strategies["strategy_4_wait"]["runnable"] is True


def test_experiment_strategy_runnable_when_module_loads():
    """strategy_2 should report runnable because the primitive is live."""
    src = CapabilityAwarenessSource()
    strategies = {s["id"]: s for s in src._strategies_snapshot()}
    assert strategies["strategy_2_experiment"]["runnable"] is True


def test_strategy_3_runnable_when_experiment_loads():
    """ask+test depends on experiment primitive for the test half."""
    src = CapabilityAwarenessSource()
    strategies = {s["id"]: s for s in src._strategies_snapshot()}
    assert strategies["strategy_3_ask_test"]["runnable"] is True


def test_each_strategy_has_how_explanation():
    """CP3: every move explains how to perform it."""
    src = CapabilityAwarenessSource()
    for s in src._strategies_snapshot():
        assert s.get("how"), f"strategy {s['id']} missing how"


# ── Experiment queue depth ───────────────────────────────────────────────────


def test_queue_depth_returns_status_counts():
    cortex, _ = _make_cortex(queue_rows=[("proposed", 3), ("observed", 1)])
    src = CapabilityAwarenessSource()
    depth = src._experiment_queue_depth(cortex)
    assert depth == {"proposed": 3, "observed": 1}


def test_queue_depth_empty_when_table_missing():
    cortex = MagicMock()
    cortex._db.side_effect = RuntimeError("no table")
    src = CapabilityAwarenessSource()
    assert src._experiment_queue_depth(cortex) == {}


def test_queue_depth_empty_when_no_rows():
    cortex, _ = _make_cortex(queue_rows=[])
    src = CapabilityAwarenessSource()
    assert src._experiment_queue_depth(cortex) == {}


# ── Tool count ───────────────────────────────────────────────────────────────


def test_tool_count_returns_integer():
    src = CapabilityAwarenessSource()
    n = src._tool_count()
    assert isinstance(n, int)
    assert n >= 0  # may be 0 in isolation


# ── push() contract ──────────────────────────────────────────────────────────


def test_push_emits_single_twm_marker():
    cortex, _ = _make_cortex(queue_rows=[("proposed", 2)])
    src = CapabilityAwarenessSource()
    obs_ids = src.push(cortex)
    assert obs_ids == [42]
    assert cortex.twm_push.call_count == 1


def test_push_marker_has_cp1_provisional():
    cortex, _ = _make_cortex()
    src = CapabilityAwarenessSource()
    src.push(cortex)
    push = cortex.twm_push.call_args
    md = push.kwargs["metadata"]
    assert md["cp1_provisional"] is True


def test_push_marker_has_category_self_capabilities():
    cortex, _ = _make_cortex()
    src = CapabilityAwarenessSource()
    src.push(cortex)
    push = cortex.twm_push.call_args
    assert push.kwargs["category"] == "self.capabilities"


def test_push_marker_includes_strategies_in_metadata():
    cortex, _ = _make_cortex()
    src = CapabilityAwarenessSource()
    src.push(cortex)
    md = cortex.twm_push.call_args.kwargs["metadata"]
    assert md["type"] == "self_capabilities"
    assert "strategies" in md
    assert len(md["strategies"]) == 4


def test_push_marker_includes_experiment_queue():
    cortex, _ = _make_cortex(queue_rows=[("proposed", 5), ("updated", 2)])
    src = CapabilityAwarenessSource()
    src.push(cortex)
    md = cortex.twm_push.call_args.kwargs["metadata"]
    assert md["experiment_queue"] == {"proposed": 5, "updated": 2}


def test_push_marker_includes_tool_count():
    cortex, _ = _make_cortex()
    src = CapabilityAwarenessSource()
    src.push(cortex)
    md = cortex.twm_push.call_args.kwargs["metadata"]
    assert "tool_count" in md
    assert isinstance(md["tool_count"], int)


# ── Interval gate ────────────────────────────────────────────────────────────


def test_push_respects_interval_gate():
    cortex, _ = _make_cortex()
    src = CapabilityAwarenessSource()
    src.push(cortex)  # first run
    # second immediate call should be gated
    result = src.push(cortex)
    assert result == []
    assert cortex.twm_push.call_count == 1


def test_push_runs_again_after_interval():
    cortex, _ = _make_cortex()
    src = CapabilityAwarenessSource()
    src.push(cortex)
    # simulate time passing
    src._last_run = datetime.now() - timedelta(seconds=200)
    src.push(cortex)
    assert cortex.twm_push.call_count == 2


# ── Failure handling ─────────────────────────────────────────────────────────


def test_push_degrades_on_twm_failure():
    cortex = MagicMock()
    cortex._db.return_value.__enter__.return_value = MagicMock()
    cortex._db.return_value.__exit__.return_value = False
    cortex._db.return_value.__enter__.return_value.fetchall.return_value = []
    cortex.twm_push.side_effect = RuntimeError("twm down")
    src = CapabilityAwarenessSource()
    # should not raise
    result = src.push(cortex)
    assert result == []


# ── Source contract ─────────────────────────────────────────────────────────


def test_source_is_slow_tier():
    """90s interval means slow tier (runs in the 300s dispatch)."""
    src = CapabilityAwarenessSource()
    assert src.TIMING_TIER == "slow"


def test_source_has_name():
    src = CapabilityAwarenessSource()
    assert src.name == "capability_awareness"
