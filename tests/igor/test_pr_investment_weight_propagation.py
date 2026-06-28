"""
test_pr_investment_weight_propagation.py — T-pr-investment-weight-propagation.

Tests that a relationship's cumulative_investment_weight modulates the
salience of its frame marker, in a bounded range that never invades the
foreground-task band.

Relationship to the original ticket framing: the ticket said "weight as
subtree salience multiplier." The frame-vs-content refinement (2026-04-13)
means subtree memories are not pushed at frame time, so 'multiplier on
subtree salience' has no direct surface to act on. Instead the weight
expresses itself through the frame marker's own salience: higher weight =
more felt (quietly), lower weight = quieter. Future ticket adds retrieval
bias for the cortex.search() side.

Tests cover:
  - pure formula: pr_compute_frame_salience maps weight to salience
  - clamping at both ends of the [0.70, 0.80] range
  - integration: _push_relationship_frame uses the formula, frame
    salience varies by facia weight
  - boundary: even the maximum weight (2.0) does not push the frame
    into the foreground-task salience band (~0.85+)
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(scope="module", autouse=True)
def ensure_seeded():
    from unseen_university.devices.igor.tools import seed_persistent_relationships as _seed

    rc = _seed.seed()
    assert rc == 0


def _reset_akien_weight_to(target: float):
    """Set PR_AKIEN cumulative_investment_weight to exactly target."""
    from unseen_university.devices.igor.tools import persistent_relationships as _pr

    row = _pr._resolve_facia("PR_AKIEN")
    if row:
        current = float(row["metadata"].get("cumulative_investment_weight", 1.0))
        delta = target - current
        if abs(delta) > 1e-9:
            _pr.pr_update_weight(name="PR_AKIEN", delta=delta)


@pytest.fixture(autouse=True)
def restore_akien_weight():
    yield
    _reset_akien_weight_to(1.0)


def _fresh_igor():
    from unseen_university.devices.igor.main import Igor
    from unseen_university.devices.igor.memory.cortex import Cortex

    inst = Igor.__new__(Igor)
    inst.cortex = Cortex(None)
    inst._pr_frame_last_push = {}
    return inst


def _clear_frame_observations():
    from unseen_university.devices.igor.memory.cortex import Cortex

    Cortex(None).twm_evict_category("relationship_frame")


# ── Pure formula ─────────────────────────────────────────────────────────────


def test_compute_frame_salience_baseline_weight_returns_default():
    from unseen_university.devices.igor.tools.persistent_relationships import pr_compute_frame_salience

    assert pr_compute_frame_salience(1.0) == pytest.approx(0.75, abs=1e-6)


def test_compute_frame_salience_high_weight_increases_to_max():
    from unseen_university.devices.igor.tools.persistent_relationships import pr_compute_frame_salience

    # Weight 2.0 (saturated) → 0.75 + (2.0 - 1.0) * 0.05 = 0.80
    assert pr_compute_frame_salience(2.0) == pytest.approx(0.80, abs=1e-6)


def test_compute_frame_salience_low_weight_decreases_to_min():
    from unseen_university.devices.igor.tools.persistent_relationships import pr_compute_frame_salience

    # Weight 0.0 (fully dormant) → 0.75 + (0.0 - 1.0) * 0.05 = 0.70
    assert pr_compute_frame_salience(0.0) == pytest.approx(0.70, abs=1e-6)


def test_compute_frame_salience_clamps_above_max():
    from unseen_university.devices.igor.tools.persistent_relationships import pr_compute_frame_salience

    # Anything above weight 2.0 still caps at 0.80
    assert pr_compute_frame_salience(5.0) == pytest.approx(0.80, abs=1e-6)
    assert pr_compute_frame_salience(100.0) == pytest.approx(0.80, abs=1e-6)


def test_compute_frame_salience_clamps_below_min():
    from unseen_university.devices.igor.tools.persistent_relationships import pr_compute_frame_salience

    # Negative weight (shouldn't happen but be safe) clamps to 0.70
    assert pr_compute_frame_salience(-1.0) == pytest.approx(0.70, abs=1e-6)
    assert pr_compute_frame_salience(-100.0) == pytest.approx(0.70, abs=1e-6)


def test_compute_frame_salience_handles_invalid_input():
    from unseen_university.devices.igor.tools.persistent_relationships import pr_compute_frame_salience

    # None or non-numeric falls back to baseline weight 1.0 → 0.75
    assert pr_compute_frame_salience(None) == pytest.approx(0.75, abs=1e-6)
    assert pr_compute_frame_salience("garbage") == pytest.approx(0.75, abs=1e-6)


def test_compute_frame_salience_never_enters_foreground_band():
    """Critical biomimetic invariant: even the maximum weight must not
    push the frame into the foreground-task salience band (~0.85+)."""
    from unseen_university.devices.igor.tools.persistent_relationships import pr_compute_frame_salience

    for w in (0.0, 0.5, 1.0, 1.5, 2.0, 3.0, 10.0):
        salience = pr_compute_frame_salience(w)
        assert (
            salience < 0.85
        ), f"weight={w} produced salience={salience} in foreground band"
        assert salience >= 0.70


# ── Integration with _push_relationship_frame ───────────────────────────────


def test_push_frame_at_baseline_weight_uses_default_salience():
    _reset_akien_weight_to(1.0)
    _clear_frame_observations()
    igor = _fresh_igor()

    igor._push_relationship_frame("PR_AKIEN", "web:shared", "turn_baseline")

    obs = igor.cortex.twm_read(
        limit=10,
        include_integrated=True,
        category="relationship_frame",
    )
    assert len(obs) == 1
    # twm_read applies age-decay to `salience` at read time, so even a fresh
    # observation's salience drifts slightly below the push-time value.
    # Assert on metadata["frame_salience"] (stamped at push time, no decay) instead.
    # The live salience just needs to be ≤ frame_salience (decay only reduces it).
    from unseen_university.devices.igor.tools.persistent_relationships import pr_compute_frame_salience

    actual_weight = float(obs[0]["metadata"].get("cumulative_investment_weight", 1.0))
    expected_salience = pr_compute_frame_salience(actual_weight)
    assert obs[0]["metadata"].get("frame_salience") == pytest.approx(
        expected_salience, abs=1e-6
    )
    assert obs[0]["salience"] <= expected_salience + 1e-6  # age-decay only reduces
    # After _reset_akien_weight_to(1.0), weight should be near baseline.
    assert actual_weight == pytest.approx(1.0, abs=0.1)


def test_push_frame_at_max_weight_uses_max_salience():
    _reset_akien_weight_to(2.0)
    _clear_frame_observations()
    igor = _fresh_igor()

    igor._push_relationship_frame("PR_AKIEN", "web:shared", "turn_high")

    obs = igor.cortex.twm_read(
        limit=10,
        include_integrated=True,
        category="relationship_frame",
    )
    assert len(obs) == 1
    # Use metadata["frame_salience"] (push-time value, no age-decay) for exact comparison.
    assert obs[0]["metadata"].get("frame_salience") == pytest.approx(0.80, abs=1e-6)


def test_push_frame_at_min_weight_uses_min_salience():
    _reset_akien_weight_to(0.0)
    _clear_frame_observations()
    igor = _fresh_igor()

    igor._push_relationship_frame("PR_AKIEN", "web:shared", "turn_low")

    obs = igor.cortex.twm_read(
        limit=10,
        include_integrated=True,
        category="relationship_frame",
    )
    assert len(obs) == 1
    # Use metadata["frame_salience"] (push-time value, no age-decay) for exact comparison.
    assert obs[0]["metadata"].get("frame_salience") == pytest.approx(0.70, abs=1e-6)


def test_push_frame_salience_varies_monotonically_with_weight():
    """A higher-weight relationship produces a frame at a strictly higher
    salience than a lower-weight relationship (for the same facia)."""
    from unseen_university.devices.igor.memory.cortex import Cortex

    weights_to_salience = {}
    for w in (0.0, 0.5, 1.0, 1.5, 2.0):
        _reset_akien_weight_to(w)
        _clear_frame_observations()
        igor = _fresh_igor()
        igor._push_relationship_frame("PR_AKIEN", "web:shared", f"turn_w{w}")
        obs = Cortex(None).twm_read(
            limit=10, include_integrated=True, category="relationship_frame"
        )
        assert len(obs) == 1
        weights_to_salience[w] = obs[0]["salience"]

    # Strictly non-decreasing as weight increases
    sorted_weights = sorted(weights_to_salience.keys())
    for a, b in zip(sorted_weights, sorted_weights[1:]):
        assert (
            weights_to_salience[a] <= weights_to_salience[b]
        ), f"salience({a})={weights_to_salience[a]} > salience({b})={weights_to_salience[b]}"
    # And strictly less at the bottom than the top
    assert weights_to_salience[0.0] < weights_to_salience[2.0]
