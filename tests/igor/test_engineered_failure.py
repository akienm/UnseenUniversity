"""
test_engineered_failure.py — T-engineered-failure-experiments

Tests for the urgency-inverts-risk-appetite policy. Under high stakes,
the walker sorts levels by information gain (maximum disambiguation)
instead of skipping low-confidence ones.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.cognition.engineered_failure import (  # noqa: E402
    STAKES_THRESHOLD,
    information_gain,
    is_high_stakes,
    sort_by_information_gain,
)
from devices.igor.cognition.experiment_cascade import (  # noqa: E402
    BaseCascadeLevel,
    CascadeResult,
    CascadeSituation,
    CascadeStatus,
    ExperimentCascade,
    Level0ExactRecall,
    Level5LLMEscalationStub,
)


def _situation(
    query: str = "urgent diagnostic", stakes: float = 0.0
) -> CascadeSituation:
    return CascadeSituation(query=query, stakes=stakes)


# ── information_gain ─────────────────────────────────────────────────────────


def test_info_gain_peaks_at_half():
    assert information_gain(0.5) == 1.0


def test_info_gain_zero_at_extremes():
    assert information_gain(0.0) == 0.0
    assert information_gain(1.0) == 0.0


def test_info_gain_monotonic_toward_half():
    assert information_gain(0.3) > information_gain(0.1)
    assert information_gain(0.7) > information_gain(0.9)


def test_info_gain_symmetric():
    assert information_gain(0.3) == pytest.approx(information_gain(0.7))
    assert information_gain(0.1) == pytest.approx(information_gain(0.9))


def test_info_gain_clamps_out_of_range():
    """Defensive: rogue predictor values shouldn't produce negative gains."""
    assert information_gain(-0.5) == 0.0
    assert information_gain(1.5) == 0.0


# ── is_high_stakes ───────────────────────────────────────────────────────────


def test_default_stakes_is_low():
    assert is_high_stakes(_situation()) is False


def test_stakes_at_threshold_is_high():
    assert is_high_stakes(_situation(stakes=STAKES_THRESHOLD)) is True


def test_stakes_above_threshold_is_high():
    assert is_high_stakes(_situation(stakes=0.9)) is True


def test_stakes_below_threshold_is_low():
    assert is_high_stakes(_situation(stakes=0.5)) is False


def test_stakes_threshold_default_is_seventy_percent():
    assert STAKES_THRESHOLD == 0.7


# ── Sort by information gain ─────────────────────────────────────────────────


class _FixedConfidenceLevel(BaseCascadeLevel):
    """Level whose predict() returns a configured constant. Test helper."""

    def __init__(self, name: str, confidence: float) -> None:
        super().__init__()
        self.name = name
        self._fixed_confidence = confidence

    def predict(self, situation):
        return self._fixed_confidence

    def try_probe(self, cortex, situation):
        return CascadeResult(
            status=CascadeStatus.MATCHED,
            level_name=self.name,
            data="fixed",
            reason="fixed level matched",
        )


def test_sort_puts_most_uncertain_first():
    """Level at 0.5 confidence has max info gain; should sort first."""
    l_certain = _FixedConfidenceLevel("certain", 0.95)
    l_uncertain = _FixedConfidenceLevel("uncertain", 0.5)
    l_doomed = _FixedConfidenceLevel("doomed", 0.05)

    ordered = sort_by_information_gain(
        [l_certain, l_uncertain, l_doomed], _situation(stakes=0.9)
    )
    assert ordered[0].name == "uncertain"  # 0.5 → gain=1.0


def test_sort_puts_near_half_confidence_before_extremes():
    l1 = _FixedConfidenceLevel("near_half", 0.6)  # gain=0.8
    l2 = _FixedConfidenceLevel("pretty_sure", 0.85)  # gain=0.3
    l3 = _FixedConfidenceLevel("pretty_doomed", 0.15)  # gain=0.3

    ordered = sort_by_information_gain([l1, l2, l3], _situation(stakes=0.9))
    assert ordered[0].name == "near_half"


def test_sort_preserves_original_order_on_ties():
    """Python's sort is stable; identical gains should stay in input order."""
    # Identical confidence → identical gain → stable sort by input index
    l1 = _FixedConfidenceLevel("first", 0.4)
    l2 = _FixedConfidenceLevel("second", 0.4)
    ordered = sort_by_information_gain([l1, l2], _situation(stakes=0.9))
    assert [lv.name for lv in ordered] == ["first", "second"]


def test_sort_handles_predictor_exceptions():
    """Level whose predict raises defaults to info_gain=1.0 (safe keep)."""

    class _BrokenPredictorLevel(BaseCascadeLevel):
        name = "broken"

        def predict(self, situation):
            raise RuntimeError("predict broke")

        def try_probe(self, cortex, situation):
            return CascadeResult(
                status=CascadeStatus.EXHAUSTED, level_name=self.name, reason=""
            )

    l_broken = _BrokenPredictorLevel()
    l_uncertain = _FixedConfidenceLevel("uncertain", 0.5)
    ordered = sort_by_information_gain([l_uncertain, l_broken], _situation(stakes=0.9))
    # Both have gain=1.0 → stable sort preserves input order
    assert ordered[0].name == "uncertain"
    assert ordered[1].name == "broken"


# ── Walker policy switch ─────────────────────────────────────────────────────


def _make_cortex():
    cortex = MagicMock()
    cortex.search.return_value = []
    cortex.twm_push.return_value = 1
    return cortex


def test_normal_stakes_uses_registration_order():
    """Low stakes → walker walks levels in registration order."""
    cortex = _make_cortex()
    cascade = ExperimentCascade(cortex)

    # Build three levels with varying confidence
    l1 = _FixedConfidenceLevel("first", 0.9)
    l2 = _FixedConfidenceLevel("second", 0.5)
    l3 = _FixedConfidenceLevel("third", 0.1)
    cascade.register(l1)
    cascade.register(l2)
    cascade.register(l3)

    # Low stakes (default) — walker keeps registration order
    active = cascade._filter_by_predictor(_situation(stakes=0.0))
    # Level 3 should be skipped (0.1 < SKIP_THRESHOLD=0.2)
    # Levels 1 and 2 in registration order
    assert [lv.name for lv in active] == ["first", "second"]


def test_high_stakes_reorders_by_info_gain():
    """High stakes → walker prefers low-confidence (max info gain) levels."""
    cortex = _make_cortex()
    cascade = ExperimentCascade(cortex)

    l_certain = _FixedConfidenceLevel("certain", 0.95)  # gain~0.1
    l_uncertain = _FixedConfidenceLevel("uncertain", 0.5)  # gain=1.0
    l_doomed = _FixedConfidenceLevel("doomed", 0.05)  # gain~0.1
    cascade.register(l_certain)
    cascade.register(l_uncertain)
    cascade.register(l_doomed)

    active = cascade._filter_by_predictor(_situation(stakes=0.9))
    # Under high stakes: sort by info gain desc
    # uncertain (gain=1.0) first, others tied at ~0.1 (stable sort by registration)
    assert active[0].name == "uncertain"


def test_high_stakes_does_not_skip_low_confidence_levels():
    """Under high stakes, every level stays active — no skipping.
    Normal stakes skips below 0.2; high stakes keeps everything.
    """
    cortex = _make_cortex()
    cascade = ExperimentCascade(cortex)

    l_high = _FixedConfidenceLevel("high", 0.9)
    l_doomed = _FixedConfidenceLevel("doomed", 0.01)
    cascade.register(l_high)
    cascade.register(l_doomed)

    active_normal = cascade._filter_by_predictor(_situation(stakes=0.0))
    active_high_stakes = cascade._filter_by_predictor(_situation(stakes=0.9))

    # Normal: doomed is skipped
    assert len(active_normal) == 1
    # High stakes: both kept
    assert len(active_high_stakes) == 2


def test_high_stakes_walker_picks_most_informative_level_first():
    """End-to-end: a high-stakes walk should actually call the highest-
    info-gain level first."""
    cortex = _make_cortex()
    cascade = ExperimentCascade(cortex)

    l_certain = _FixedConfidenceLevel("certain", 0.95)
    l_uncertain = _FixedConfidenceLevel("uncertain", 0.5)

    call_order: list[str] = []

    class _Tracker(_FixedConfidenceLevel):
        def try_probe(self, cortex, situation):
            call_order.append(self.name)
            return CascadeResult(
                status=CascadeStatus.MATCHED,
                level_name=self.name,
                data="hit",
                reason="hit",
            )

    t_certain = _Tracker("t_certain", 0.95)
    t_uncertain = _Tracker("t_uncertain", 0.5)

    cascade.register(t_certain)
    cascade.register(t_uncertain)

    # High stakes: uncertain should be called first and match; certain never
    result = cascade.attempt(_situation("urgent", stakes=0.9))
    assert result.status == CascadeStatus.MATCHED
    assert call_order == ["t_uncertain"]


def test_normal_stakes_walker_uses_registration_order():
    """End-to-end: normal stakes walks levels in registration order."""
    cortex = _make_cortex()
    cascade = ExperimentCascade(cortex)

    call_order: list[str] = []

    class _Tracker(_FixedConfidenceLevel):
        def try_probe(self, cortex, situation):
            call_order.append(self.name)
            return CascadeResult(
                status=CascadeStatus.MATCHED,
                level_name=self.name,
                data="hit",
                reason="hit",
            )

    # Certain registered first, uncertain second
    t_certain = _Tracker("t_certain", 0.95)
    t_uncertain = _Tracker("t_uncertain", 0.5)
    cascade.register(t_certain)
    cascade.register(t_uncertain)

    # Normal stakes: certain matches first (registration order)
    result = cascade.attempt(_situation("routine", stakes=0.0))
    assert call_order == ["t_certain"]


# ── Stakes field on CascadeSituation ─────────────────────────────────────────


def test_stakes_default_zero():
    s = CascadeSituation(query="x")
    assert s.stakes == 0.0


def test_stakes_explicit():
    s = CascadeSituation(query="x", stakes=0.85)
    assert s.stakes == 0.85


# ── Regression: existing cascade behavior untouched at low stakes ───────────


def test_existing_low_stakes_match_behavior_unchanged():
    """Sanity: low-stakes cascade on default levels still matches at L0."""
    cortex = MagicMock()
    cortex.search.return_value = [{"id": "hit"}]
    cortex.twm_push.return_value = 1

    cascade = ExperimentCascade(cortex)
    cascade.register(Level0ExactRecall())
    cascade.register(Level5LLMEscalationStub())

    result = cascade.attempt(_situation())  # stakes=0.0
    assert result.status == CascadeStatus.MATCHED
    assert result.level_name == "level_0_exact_recall"
