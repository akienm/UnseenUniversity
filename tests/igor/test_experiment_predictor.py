"""
test_experiment_predictor.py — T-experiment-predictor-primitive

Tests for the SignaturePredictor + walker predictor integration.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.cognition.experiment_cascade import (  # noqa: E402
    BaseCascadeLevel,
    CascadeResult,
    CascadeSituation,
    CascadeStatus,
    ExperimentCascade,
    Level0ExactRecall,
    Level1WidenOnMiss,
    Level5LLMEscalationStub,
    _StubLevel,
    build_default_cascade,
)
from devices.igor.cognition.experiment_predictor import (  # noqa: E402
    INITIAL_CONFIDENCE,
    MIN_TOKEN_LEN,
    SKIP_THRESHOLD,
    SignaturePredictor,
    _signature,
)


def _situation(query: str = "igor dev facia") -> CascadeSituation:
    return CascadeSituation(query=query)


# ── _signature ───────────────────────────────────────────────────────────────


def test_signature_is_sorted_unique_tokens():
    sig = _signature(_situation("dev igor dev facia"))
    assert sig == ("dev", "facia", "igor")


def test_signature_drops_short_tokens():
    """Tokens below MIN_TOKEN_LEN (3) are filtered out of signatures."""
    sig = _signature(_situation("a is at an igor"))
    assert "a" not in sig
    assert "is" not in sig
    assert "at" not in sig
    assert "an" not in sig
    assert "igor" in sig


def test_signature_lowercases():
    sig = _signature(_situation("IGOR Dev"))
    assert "igor" in sig
    assert "dev" in sig
    assert "IGOR" not in sig


def test_signature_empty_query_returns_empty_tuple():
    assert _signature(_situation("")) == ()


def test_signature_is_deterministic_across_phrasing_order():
    """Same token set in any order produces the same signature."""
    a = _signature(_situation("igor dev facia"))
    b = _signature(_situation("facia igor dev"))
    c = _signature(_situation("dev facia igor"))
    assert a == b == c


# ── SignaturePredictor basics ────────────────────────────────────────────────


def test_predictor_starts_at_initial_confidence():
    p = SignaturePredictor()
    assert p.predict(_situation()) == INITIAL_CONFIDENCE


def test_initial_confidence_is_half():
    assert INITIAL_CONFIDENCE == 0.5


def test_skip_threshold_is_low():
    """Default skip threshold should be strict-ish — only skip levels
    we're pretty sure won't match."""
    assert 0.0 < SKIP_THRESHOLD < 0.5


def test_min_token_len_is_three():
    assert MIN_TOKEN_LEN == 3


# ── Hebbian update ───────────────────────────────────────────────────────────


def test_single_match_pulls_prediction_toward_one():
    p = SignaturePredictor()
    s = _situation("goal graph lookup")
    p.train(s, matched=True)
    assert p.predict(s) == 1.0


def test_single_miss_pulls_prediction_toward_zero():
    p = SignaturePredictor()
    s = _situation("goal graph lookup")
    p.train(s, matched=False)
    assert p.predict(s) == 0.0


def test_mixed_hits_and_misses_averages():
    p = SignaturePredictor()
    s = _situation("goal graph lookup")
    for _ in range(7):
        p.train(s, matched=True)
    for _ in range(3):
        p.train(s, matched=False)
    assert p.predict(s) == pytest.approx(0.7, rel=1e-3)


def test_training_one_signature_does_not_affect_another():
    p = SignaturePredictor()
    s1 = _situation("alpha bravo charlie")
    s2 = _situation("delta echo foxtrot")
    p.train(s1, matched=True)
    assert p.predict(s1) == 1.0
    assert p.predict(s2) == INITIAL_CONFIDENCE


def test_same_signature_different_phrasing_shares_training():
    p = SignaturePredictor()
    p.train(_situation("igor dev facia"), matched=True)
    # Same tokens, different order and casing
    confidence = p.predict(_situation("Facia IGOR dev"))
    assert confidence == 1.0


# ── Overall hit rate / stats ─────────────────────────────────────────────────


def test_overall_hit_rate_empty_predictor():
    p = SignaturePredictor()
    assert p.overall_hit_rate() == INITIAL_CONFIDENCE


def test_overall_hit_rate_aggregates_across_signatures():
    p = SignaturePredictor()
    p.train(_situation("alpha beta gamma"), matched=True)
    p.train(_situation("alpha beta gamma"), matched=True)
    p.train(_situation("delta echo foxtrot"), matched=False)
    p.train(_situation("hotel india juliet"), matched=False)
    # 2 hits, 2 misses total → 0.5
    assert p.overall_hit_rate() == pytest.approx(0.5)


def test_stats_reports_signature_count():
    p = SignaturePredictor()
    p.train(_situation("alpha bravo charlie"), matched=True)
    p.train(_situation("delta echo foxtrot"), matched=False)
    stats = p.stats()
    assert stats["signatures_tracked"] == 2
    assert stats["total_hits"] == 1
    assert stats["total_misses"] == 1


def test_reset_clears_predictor_memory():
    p = SignaturePredictor()
    p.train(_situation("alpha bravo charlie"), matched=True)
    p.reset()
    assert p.predict(_situation("alpha bravo charlie")) == INITIAL_CONFIDENCE
    assert p.stats()["signatures_tracked"] == 0


# ── BaseCascadeLevel has a predictor ─────────────────────────────────────────


def test_base_level_exposes_predict_and_train():
    level = Level0ExactRecall()
    assert level.predict(_situation()) == INITIAL_CONFIDENCE
    level.train(_situation(), matched=True)
    assert level.predict(_situation()) == 1.0


def test_stub_level_has_its_own_predictor():
    stub_a = _StubLevel(name="stub_a", reason="x")
    stub_b = _StubLevel(name="stub_b", reason="y")
    stub_a.train(_situation("alpha bravo"), matched=True)
    assert stub_a.predict(_situation("alpha bravo")) == 1.0
    # stub_b is independent
    assert stub_b.predict(_situation("alpha bravo")) == INITIAL_CONFIDENCE


# ── Walker uses predictor ────────────────────────────────────────────────────


def _make_mock_cortex(search_results=None):
    cortex = MagicMock()
    cortex.search.return_value = search_results or []
    cortex.twm_push.return_value = 1
    return cortex


def test_walker_skips_levels_below_skip_threshold():
    """Train a level's predictor down; walker should skip it next run."""
    cortex = _make_mock_cortex(search_results=[])
    cascade = ExperimentCascade(cortex)

    # Train level 0 down to 0.0 for this signature
    level0 = Level0ExactRecall()
    for _ in range(5):
        level0.train(_situation("alpha bravo charlie"), matched=False)
    assert level0.predict(_situation("alpha bravo charlie")) == 0.0

    # Level 5 always returns ESCALATE
    level5 = Level5LLMEscalationStub()

    cascade.register(level0)
    cascade.register(level5)

    result = cascade.attempt(_situation("alpha bravo charlie"))
    assert result.status == CascadeStatus.ESCALATE
    # Level 0 should have been skipped — cortex.search never called
    cortex.search.assert_not_called()


def test_walker_does_not_skip_at_initial_confidence():
    """Fresh predictor at 0.5 is above skip threshold; level runs."""
    cortex = _make_mock_cortex(search_results=[{"id": "hit"}])
    cascade = ExperimentCascade(cortex)
    cascade.register(Level0ExactRecall())
    cascade.register(Level5LLMEscalationStub())

    result = cascade.attempt(_situation())
    assert result.status == CascadeStatus.MATCHED
    cortex.search.assert_called_once()


def test_walker_trains_level_on_match():
    """After a MATCHED result, the level's predictor should bias toward match."""
    cortex = _make_mock_cortex(search_results=[{"id": "hit"}])
    level0 = Level0ExactRecall()
    cascade = ExperimentCascade(cortex)
    cascade.register(level0)
    cascade.register(Level5LLMEscalationStub())

    # Before: fresh at 0.5
    assert level0.predict(_situation("alpha bravo charlie")) == INITIAL_CONFIDENCE

    cascade.attempt(_situation("alpha bravo charlie"))

    # After: pulled toward 1.0
    assert level0.predict(_situation("alpha bravo charlie")) == 1.0


def test_walker_trains_level_on_exhaustion():
    """After EXHAUSTED, the level's predictor should bias away from match."""
    cortex = _make_mock_cortex(search_results=[])
    level0 = Level0ExactRecall()
    cascade = ExperimentCascade(cortex)
    cascade.register(level0)
    cascade.register(Level5LLMEscalationStub())

    cascade.attempt(_situation("alpha bravo charlie"))
    assert level0.predict(_situation("alpha bravo charlie")) == 0.0


def test_walker_trains_level5_on_escalate_as_match():
    """Level 5's ESCALATE counts as match — it's the escalation level's
    success shape. Over many runs, its predictor should stay near 1.0."""
    cortex = _make_mock_cortex(search_results=[])
    level5 = Level5LLMEscalationStub()
    cascade = ExperimentCascade(cortex)
    cascade.register(level5)

    cascade.attempt(_situation("alpha bravo charlie"))
    assert level5.predict(_situation("alpha bravo charlie")) == 1.0


def test_walker_floor_rule_runs_all_when_predictors_would_skip_all():
    """If every level's predictor says skip, walker runs all of them anyway.
    CP1: never silently drop the whole cascade.
    """
    cortex = _make_mock_cortex(search_results=[{"id": "hit"}])
    cascade = ExperimentCascade(cortex)

    level0 = Level0ExactRecall()
    level5 = Level5LLMEscalationStub()
    # Train both down hard
    for _ in range(20):
        level0.train(_situation("alpha bravo charlie"), matched=False)
        level5.train(_situation("alpha bravo charlie"), matched=False)
    assert level0.predict(_situation("alpha bravo charlie")) == 0.0
    assert level5.predict(_situation("alpha bravo charlie")) == 0.0

    cascade.register(level0)
    cascade.register(level5)

    # Should still run Level 0 (which matches)
    result = cascade.attempt(_situation("alpha bravo charlie"))
    assert result.status == CascadeStatus.MATCHED
    cortex.search.assert_called_once()


def test_walker_survives_broken_predictor():
    """A level whose predict() raises defaults to being kept (safe fallback)."""
    cortex = _make_mock_cortex(search_results=[{"id": "hit"}])
    cascade = ExperimentCascade(cortex)

    class BadPredictorLevel(BaseCascadeLevel):
        name = "bad_predictor"

        def predict(self, situation):
            raise RuntimeError("predictor broke")

        def try_probe(self, cortex, situation):
            return CascadeResult(
                status=CascadeStatus.MATCHED,
                level_name=self.name,
                data=["result"],
                reason="matched",
            )

    cascade.register(BadPredictorLevel())
    result = cascade.attempt(_situation())
    # Walker should still run this level even though predict() raised
    assert result.status == CascadeStatus.MATCHED
    assert result.level_name == "bad_predictor"


def test_walker_survives_broken_train():
    """A level whose train() raises should not crash the walker."""
    cortex = _make_mock_cortex(search_results=[{"id": "hit"}])
    cascade = ExperimentCascade(cortex)

    class BadTrainLevel(BaseCascadeLevel):
        name = "bad_train"

        def train(self, situation, matched):
            raise RuntimeError("train broke")

        def try_probe(self, cortex, situation):
            return CascadeResult(
                status=CascadeStatus.MATCHED,
                level_name=self.name,
                data=["result"],
                reason="matched",
            )

    cascade.register(BadTrainLevel())
    # Should not raise
    result = cascade.attempt(_situation())
    assert result.status == CascadeStatus.MATCHED


# ── Graduation story (end-to-end simulation) ─────────────────────────────────


def test_level_graduates_to_skip_after_consistent_misses():
    """Simulate: Level 0 consistently misses 'complex query X' but Level 1
    widen catches it. Over time, Level 0's predictor for that signature
    drops; the walker starts skipping Level 0 → straight to Level 1.
    This is the compounding efficiency story.
    """
    cortex = _make_mock_cortex(search_results=[])  # Level 0 always empty
    level0 = Level0ExactRecall()
    level1 = Level1WidenOnMiss()
    cascade = ExperimentCascade(cortex)
    cascade.register(level0)
    cascade.register(level1)

    mock_mem = MagicMock()
    mock_mem.id = "PR_IGORS_PROJECT"

    # Run the cascade 5 times with the same query; Level 1 always matches
    with patch(
        "devices.igor.memory.search_widen.widen_search",
        return_value=([mock_mem], "token_like"),
    ):
        for _ in range(5):
            cascade.attempt(_situation("igor dev facia"))

    # Level 0's predictor for this signature should have dropped
    assert level0.predict(_situation("igor dev facia")) == 0.0

    # On the next run, Level 0 should be skipped entirely
    cortex.search.reset_mock()
    with patch(
        "devices.igor.memory.search_widen.widen_search",
        return_value=([mock_mem], "token_like"),
    ):
        result = cascade.attempt(_situation("igor dev facia"))
    assert result.status == CascadeStatus.MATCHED
    assert result.level_name == "level_1_widen_on_miss"
    cortex.search.assert_not_called()  # Level 0 was skipped


# ── build_default_cascade still works with predictors ───────────────────────


def test_default_cascade_levels_all_have_predictors():
    cortex = _make_mock_cortex()
    cascade = build_default_cascade(cortex)
    for level in cascade._levels:
        # Every level has a predictor that starts at INITIAL_CONFIDENCE
        assert level.predict(_situation()) == INITIAL_CONFIDENCE
