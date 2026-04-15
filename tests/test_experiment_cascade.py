"""
test_experiment_cascade.py — T-substrate-experiment-cascade

Tests for the cascade walker + Level 0 + Level 1 + stub levels +
escalation stub. Mocked cortex throughout.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition.experiment_cascade import (  # noqa: E402
    CascadeResult,
    CascadeSituation,
    CascadeStatus,
    DEFAULT_LEVEL_BUDGET,
    ExperimentCascade,
    Level0ExactRecall,
    Level1WidenOnMiss,
    Level5LLMEscalationStub,
    _StubLevel,
    build_default_cascade,
)
from wild_igor.igor.cognition.experiment import (  # noqa: E402
    ExperimentStatus,
    Outcome,
)


def _make_mock_cortex(search_results=None):
    cortex = MagicMock()
    cortex.search.return_value = search_results or []
    cortex.twm_push.return_value = 1
    return cortex


def _situation(query: str = "igor dev facia") -> CascadeSituation:
    return CascadeSituation(query=query, target_shape="facia")


# ── CascadeResult ────────────────────────────────────────────────────────────


def test_result_matched_is_terminal():
    r = CascadeResult(status=CascadeStatus.MATCHED, level_name="x")
    assert r.is_terminal() is True


def test_result_escalate_is_terminal():
    r = CascadeResult(status=CascadeStatus.ESCALATE, level_name="x")
    assert r.is_terminal() is True


def test_result_exhausted_is_not_terminal():
    r = CascadeResult(status=CascadeStatus.EXHAUSTED, level_name="x")
    assert r.is_terminal() is False


def test_result_leveraged_is_not_terminal():
    r = CascadeResult(status=CascadeStatus.LEVERAGED, level_name="x")
    assert r.is_terminal() is False


# ── Level 0: exact recall ────────────────────────────────────────────────────


def test_level0_matches_when_cortex_search_returns_results():
    cortex = _make_mock_cortex(search_results=[{"id": "mem_1"}, {"id": "mem_2"}])
    level = Level0ExactRecall()
    result = level.try_probe(cortex, _situation())
    assert result.status == CascadeStatus.MATCHED
    assert result.level_name == "level_0_exact_recall"
    assert len(result.data) == 2
    assert result.experiment is not None
    assert result.experiment.observation.outcome == Outcome.MATCH


def test_level0_exhausted_when_cortex_search_empty():
    cortex = _make_mock_cortex(search_results=[])
    level = Level0ExactRecall()
    result = level.try_probe(cortex, _situation())
    assert result.status == CascadeStatus.EXHAUSTED
    assert result.experiment.observation.outcome == Outcome.INCONCLUSIVE


def test_level0_exhausted_when_cortex_search_raises():
    cortex = _make_mock_cortex()
    cortex.search.side_effect = RuntimeError("db down")
    level = Level0ExactRecall()
    result = level.try_probe(cortex, _situation())
    assert result.status == CascadeStatus.EXHAUSTED
    assert "db down" in result.experiment.observation.data.get(
        "detail", ""
    ) or "RuntimeError" in result.experiment.observation.data.get("error", "")


def test_level0_experiment_lifecycle_advances():
    """Level 0 must advance the experiment to OBSERVED state."""
    cortex = _make_mock_cortex(search_results=[{"id": "x"}])
    level = Level0ExactRecall()
    result = level.try_probe(cortex, _situation())
    assert result.experiment.status == ExperimentStatus.OBSERVED


# ── Level 1: widen-on-miss ───────────────────────────────────────────────────


def test_level1_matches_via_widen_search():
    cortex = _make_mock_cortex()
    level = Level1WidenOnMiss()
    mock_memory = MagicMock()
    mock_memory.id = "PR_IGORS_PROJECT"
    with patch(
        "wild_igor.igor.memory.search_widen.widen_search",
        return_value=([mock_memory], "token_like"),
    ):
        result = level.try_probe(cortex, _situation())
    assert result.status == CascadeStatus.MATCHED
    assert result.experiment.observation.data["strategy"] == "token_like"
    assert result.experiment.observation.data["result_count"] == 1


def test_level1_exhausted_when_widen_returns_empty():
    cortex = _make_mock_cortex()
    level = Level1WidenOnMiss()
    with patch(
        "wild_igor.igor.memory.search_widen.widen_search",
        return_value=([], None),
    ):
        result = level.try_probe(cortex, _situation())
    assert result.status == CascadeStatus.EXHAUSTED


def test_level1_exhausted_when_widen_raises():
    cortex = _make_mock_cortex()
    level = Level1WidenOnMiss()
    with patch(
        "wild_igor.igor.memory.search_widen.widen_search",
        side_effect=RuntimeError("widen broke"),
    ):
        result = level.try_probe(cortex, _situation())
    assert result.status == CascadeStatus.EXHAUSTED


# ── Stub levels ──────────────────────────────────────────────────────────────


def test_stub_level_always_exhausted():
    level = _StubLevel(name="stub", reason="not yet wired")
    result = level.try_probe(_make_mock_cortex(), _situation())
    assert result.status == CascadeStatus.EXHAUSTED
    assert result.level_name == "stub"
    assert result.reason == "not yet wired"


def test_level5_llm_stub_returns_escalate():
    cortex = _make_mock_cortex()
    level = Level5LLMEscalationStub()
    result = level.try_probe(cortex, _situation("help me plan"))
    assert result.status == CascadeStatus.ESCALATE
    assert result.level_name == "level_5_llm_reasoning"
    assert result.data["query"] == "help me plan"
    assert "handoff_ts" in result.data


# ── The walker ───────────────────────────────────────────────────────────────


def test_walker_empty_registration_returns_exhausted():
    cortex = _make_mock_cortex()
    cascade = ExperimentCascade(cortex)
    result = cascade.attempt(_situation())
    assert result.status == CascadeStatus.EXHAUSTED
    assert result.level_name == "no_levels_registered"


def test_walker_stops_on_first_match():
    cortex = _make_mock_cortex(search_results=[{"id": "hit"}])
    cascade = ExperimentCascade(cortex)
    cascade.register(Level0ExactRecall())
    cascade.register(Level1WidenOnMiss())  # should NOT be tried
    cascade.register(Level5LLMEscalationStub())  # should NOT be tried

    result = cascade.attempt(_situation())
    assert result.status == CascadeStatus.MATCHED
    assert result.level_name == "level_0_exact_recall"
    # widen + llm stubs should not have been touched
    cortex.search.assert_called_once()


def test_walker_advances_through_exhausted_levels():
    """Levels 0+1 empty → walker reaches Level 5 stub → ESCALATE."""
    cortex = _make_mock_cortex(search_results=[])
    cascade = ExperimentCascade(cortex)
    cascade.register(Level0ExactRecall())
    cascade.register(Level1WidenOnMiss())
    cascade.register(Level5LLMEscalationStub())

    with patch(
        "wild_igor.igor.memory.search_widen.widen_search",
        return_value=([], None),
    ):
        result = cascade.attempt(_situation())

    assert result.status == CascadeStatus.ESCALATE
    assert result.level_name == "level_5_llm_reasoning"


def test_walker_escalates_when_all_levels_exhausted_no_llm():
    cortex = _make_mock_cortex(search_results=[])
    cascade = ExperimentCascade(cortex)
    cascade.register(Level0ExactRecall())

    result = cascade.attempt(_situation())
    # No Level 5 registered → all levels exhausted
    assert result.status == CascadeStatus.EXHAUSTED
    assert result.level_name == "all_levels_exhausted"


def test_walker_pushes_twm_marker_on_each_outcome():
    cortex = _make_mock_cortex(search_results=[{"id": "hit"}])
    cascade = ExperimentCascade(cortex)
    cascade.register(Level0ExactRecall())

    cascade.attempt(_situation())
    assert cortex.twm_push.called
    push = cortex.twm_push.call_args
    assert push.kwargs["category"] == "cascade_walk"
    md = push.kwargs["metadata"]
    assert md["type"] == "cascade_walk"
    assert md["status"] == "matched"


def test_walker_budget_exhaustion():
    cortex = _make_mock_cortex(search_results=[])
    cascade = ExperimentCascade(cortex, level_budget=2)
    # Register 5 stub levels — budget of 2 will run out
    for i in range(5):
        cascade.register(
            _StubLevel(name=f"stub_{i}", reason="no-op"),
        )
    result = cascade.attempt(_situation())
    # 2 calls consumed by stubs, inner-for exits with break,
    # outer while re-enters, budget dips below 0 → exhausted branch
    assert result.status == CascadeStatus.EXHAUSTED


def test_walker_handles_level_exception_gracefully():
    """A level that raises shouldn't crash the walker; treated as exhausted."""
    from wild_igor.igor.cognition.experiment_cascade import BaseCascadeLevel

    cortex = _make_mock_cortex()

    class BrokenLevel(BaseCascadeLevel):
        name = "broken"

        def try_probe(self, cortex, situation):
            raise RuntimeError("oops")

    cascade = ExperimentCascade(cortex)
    cascade.register(BrokenLevel())
    cascade.register(Level5LLMEscalationStub())

    result = cascade.attempt(_situation())
    assert result.status == CascadeStatus.ESCALATE
    assert result.level_name == "level_5_llm_reasoning"


def test_walker_default_budget():
    assert DEFAULT_LEVEL_BUDGET == 10


# ── build_default_cascade ────────────────────────────────────────────────────


def test_default_cascade_has_six_levels():
    cortex = _make_mock_cortex()
    cascade = build_default_cascade(cortex)
    assert len(cascade._levels) == 6


def test_default_cascade_level_order():
    cortex = _make_mock_cortex()
    cascade = build_default_cascade(cortex)
    names = [lv.name for lv in cascade._levels]
    assert names == [
        "level_0_exact_recall",
        "level_1_widen_on_miss",
        "level_2_interpretive_traversal",
        "level_3_tool_combination",
        "level_4_past_experiment_lookup",
        "level_5_llm_reasoning",
    ]


def test_default_cascade_walks_to_llm_when_everything_empty():
    """Full walk: L0 empty → L1 empty → L2/3/4 stubs exhausted → L5 escalate."""
    cortex = _make_mock_cortex(search_results=[])
    cascade = build_default_cascade(cortex)
    with patch(
        "wild_igor.igor.memory.search_widen.widen_search",
        return_value=([], None),
    ):
        result = cascade.attempt(_situation())
    assert result.status == CascadeStatus.ESCALATE
    assert result.level_name == "level_5_llm_reasoning"


def test_default_cascade_matches_at_level_0_when_recall_hits():
    cortex = _make_mock_cortex(search_results=[{"id": "direct_hit"}])
    cascade = build_default_cascade(cortex)
    result = cascade.attempt(_situation())
    assert result.status == CascadeStatus.MATCHED
    assert result.level_name == "level_0_exact_recall"


def test_default_cascade_matches_at_level_1_when_widen_hits():
    cortex = _make_mock_cortex(search_results=[])  # L0 empty
    cascade = build_default_cascade(cortex)
    mock_memory = MagicMock()
    mock_memory.id = "PR_IGORS_PROJECT"
    with patch(
        "wild_igor.igor.memory.search_widen.widen_search",
        return_value=([mock_memory], "token_like"),
    ):
        result = cascade.attempt(_situation("igor dev"))
    assert result.status == CascadeStatus.MATCHED
    assert result.level_name == "level_1_widen_on_miss"


# ── CascadeSituation ─────────────────────────────────────────────────────────


def test_situation_defaults():
    s = CascadeSituation(query="x")
    assert s.query == "x"
    assert s.context == {}
    assert s.target_shape == "any"
