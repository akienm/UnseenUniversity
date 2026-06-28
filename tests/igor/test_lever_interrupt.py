"""
test_lever_interrupt.py — T-lever-interrupt-pattern

Tests for the Lever dataclass, the walker's LEVERAGED handler, and the
lever-interrupt budget. The pattern: a level can surface an unexpected
anchor mid-probe, the walker aborts, builds a new situation enriched
with the lever, and restarts the cascade. Budget prevents infinite
flipping between competing anchors.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unseen_university.devices.igor.cognition.experiment_cascade import (  # noqa: E402
    BaseCascadeLevel,
    CascadeResult,
    CascadeSituation,
    CascadeStatus,
    DEFAULT_LEVER_BUDGET,
    ExperimentCascade,
    Lever,
    Level0ExactRecall,
    Level5LLMEscalationStub,
)


def _situation(query: str = "resolve the goal tree") -> CascadeSituation:
    return CascadeSituation(query=query)


def _make_cortex(search_results=None):
    cortex = MagicMock()
    cortex.search.return_value = search_results or []
    cortex.twm_push.return_value = 1
    return cortex


# ── Lever dataclass ──────────────────────────────────────────────────────────


def test_lever_minimal_construction():
    lever = Lever(anchor_id="PR_IGORS_PROJECT")
    assert lever.anchor_id == "PR_IGORS_PROJECT"
    assert lever.anchor_type == "unknown"
    assert lever.narrative == ""
    assert lever.relevance == 0.0
    assert lever.new_query_seed is None


def test_lever_full_construction():
    lever = Lever(
        anchor_id="INTERP_FACIA_goal_decompose",
        anchor_type="tool",
        narrative="found goal_decompose tool — use it instead of manual traversal",
        relevance=0.85,
        new_query_seed="goal_decompose PR_IGORS_PROJECT",
    )
    assert lever.anchor_type == "tool"
    assert lever.relevance == 0.85
    assert lever.new_query_seed == "goal_decompose PR_IGORS_PROJECT"


# ── Test fixtures: programmable levels ──────────────────────────────────────


class _ProgrammableLevel(BaseCascadeLevel):
    """Test level that returns a scripted sequence of CascadeResults.

    The walker calls try_probe repeatedly as the cascade restarts, so
    we pop from a queue of scripted results.
    """

    def __init__(self, name: str, scripted_results: list[CascadeResult]) -> None:
        super().__init__()
        self.name = name
        self._scripted = list(scripted_results)
        self.call_count = 0
        self.situations_seen: list[CascadeSituation] = []

    def try_probe(self, cortex, situation):
        self.call_count += 1
        self.situations_seen.append(situation)
        if not self._scripted:
            return CascadeResult(
                status=CascadeStatus.EXHAUSTED,
                level_name=self.name,
                reason="script empty",
            )
        result = self._scripted.pop(0)
        # Scripted results have level_name set to match this level
        result.level_name = self.name
        return result


def _exhausted() -> CascadeResult:
    return CascadeResult(
        status=CascadeStatus.EXHAUSTED, level_name="", reason="exhausted"
    )


def _matched(data="hit") -> CascadeResult:
    return CascadeResult(
        status=CascadeStatus.MATCHED, level_name="", data=data, reason="matched"
    )


def _leveraged(lever: Lever) -> CascadeResult:
    return CascadeResult(
        status=CascadeStatus.LEVERAGED,
        level_name="",
        lever=lever,
        reason=f"lever surfaced: {lever.anchor_id}",
    )


# ── Walker accepts a LEVERAGED result and restarts ───────────────────────────


def test_lever_interrupt_restarts_cascade_with_enriched_situation():
    cortex = _make_cortex()
    cascade = ExperimentCascade(cortex)

    lever = Lever(
        anchor_id="PR_IGORS_PROJECT",
        anchor_type="facia",
        narrative="found the project facia — restart with this anchor",
        relevance=0.9,
    )

    # First call returns LEVERAGED; second call (after restart) matches
    level = _ProgrammableLevel(
        name="lever_level",
        scripted_results=[_leveraged(lever), _matched("final hit")],
    )
    cascade.register(level)

    result = cascade.attempt(_situation("find the thing"))
    assert result.status == CascadeStatus.MATCHED
    assert result.data == "final hit"
    assert level.call_count == 2

    # The second situation should have the lever in its context
    second = level.situations_seen[1]
    assert "lever_chain" in second.context
    assert len(second.context["lever_chain"]) == 1
    assert second.context["lever_chain"][0]["anchor_id"] == "PR_IGORS_PROJECT"
    assert second.context["latest_lever"] == "PR_IGORS_PROJECT"


def test_lever_preserves_original_query_when_no_seed():
    cortex = _make_cortex()
    cascade = ExperimentCascade(cortex)

    lever = Lever(anchor_id="X", anchor_type="facia")  # no new_query_seed
    level = _ProgrammableLevel(
        name="lvl", scripted_results=[_leveraged(lever), _matched()]
    )
    cascade.register(level)

    cascade.attempt(_situation("original query"))
    # Second call should have the SAME query
    assert level.situations_seen[1].query == "original query"


def test_lever_with_new_query_seed_replaces_query():
    cortex = _make_cortex()
    cascade = ExperimentCascade(cortex)

    lever = Lever(
        anchor_id="goal_decompose",
        anchor_type="tool",
        new_query_seed="goal_decompose PR_IGORS_PROJECT",
    )
    level = _ProgrammableLevel(
        name="lvl", scripted_results=[_leveraged(lever), _matched()]
    )
    cascade.register(level)

    cascade.attempt(_situation("original query"))
    # Second call should have the new seed as query
    assert level.situations_seen[1].query == "goal_decompose PR_IGORS_PROJECT"


# ── Lever chain accumulates across multiple interrupts ──────────────────────


def test_multiple_levers_accumulate_in_chain():
    cortex = _make_cortex()
    cascade = ExperimentCascade(cortex)

    lever_a = Lever(anchor_id="A", anchor_type="facia")
    lever_b = Lever(anchor_id="B", anchor_type="tool")
    level = _ProgrammableLevel(
        name="lvl",
        scripted_results=[_leveraged(lever_a), _leveraged(lever_b), _matched()],
    )
    cascade.register(level)

    cascade.attempt(_situation("q"))
    third = level.situations_seen[2]
    chain = third.context["lever_chain"]
    assert len(chain) == 2
    assert chain[0]["anchor_id"] == "A"
    assert chain[1]["anchor_id"] == "B"
    assert third.context["latest_lever"] == "B"


# ── Lever budget caps interrupts ─────────────────────────────────────────────


def test_lever_budget_default():
    assert DEFAULT_LEVER_BUDGET == 3


def test_lever_budget_demotes_excess_leverages_to_exhausted():
    cortex = _make_cortex()
    cascade = ExperimentCascade(cortex, lever_budget=2)

    lever = Lever(anchor_id="X")
    # Script 4 levers — only first 2 should be honored; 3rd and 4th
    # should be demoted to EXHAUSTED in the walker's inner loop.
    level = _ProgrammableLevel(
        name="lvl",
        scripted_results=[
            _leveraged(lever),  # honored, restart
            _leveraged(lever),  # honored, restart
            _leveraged(lever),  # demoted to EXHAUSTED
            _leveraged(lever),  # demoted to EXHAUSTED
        ],
    )
    cascade.register(level)
    cascade.register(Level5LLMEscalationStub())  # fallback escalation

    result = cascade.attempt(_situation("q"))
    # After budget exhaustion, the walker either escalates via level 5
    # or returns EXHAUSTED — either way it stops processing levers
    assert result.status in (CascadeStatus.ESCALATE, CascadeStatus.EXHAUSTED)


def test_lever_budget_zero_means_no_levers_honored():
    cortex = _make_cortex()
    cascade = ExperimentCascade(cortex, lever_budget=0)

    lever = Lever(anchor_id="X")
    level = _ProgrammableLevel(
        name="lvl", scripted_results=[_leveraged(lever), _matched()]
    )
    cascade.register(level)
    cascade.register(Level5LLMEscalationStub())

    result = cascade.attempt(_situation("q"))
    # The leveraged result gets demoted to EXHAUSTED; walker continues
    # to Level 5 stub which escalates
    assert result.status == CascadeStatus.ESCALATE


# ── Lever TWM marker ─────────────────────────────────────────────────────────


def test_lever_interrupt_pushes_twm_marker():
    cortex = _make_cortex()
    cascade = ExperimentCascade(cortex)

    lever = Lever(
        anchor_id="PR_IGORS_PROJECT",
        anchor_type="facia",
        narrative="found the project",
        relevance=0.85,
    )
    level = _ProgrammableLevel(
        name="lvl", scripted_results=[_leveraged(lever), _matched()]
    )
    cascade.register(level)

    cascade.attempt(_situation("q"))

    # cortex.twm_push called multiple times (lever marker + final outcome)
    # Find the lever one
    lever_calls = [
        call
        for call in cortex.twm_push.call_args_list
        if call.kwargs.get("category") == "cascade_lever"
    ]
    assert len(lever_calls) == 1
    md = lever_calls[0].kwargs["metadata"]
    assert md["type"] == "cascade_lever_interrupt"
    assert md["anchor_id"] == "PR_IGORS_PROJECT"
    assert md["anchor_type"] == "facia"
    assert md["narrative"] == "found the project"
    assert md["relevance"] == 0.85
    assert md["cp1_provisional"] is True


# ── Lever without lever field (fallback) ─────────────────────────────────────


def test_leveraged_without_lever_field_does_not_crash():
    """If a level returns LEVERAGED without populating .lever, the walker
    should not crash — just consume a budget slot and keep going."""
    cortex = _make_cortex()
    cascade = ExperimentCascade(cortex)

    bare_leveraged = CascadeResult(
        status=CascadeStatus.LEVERAGED,
        level_name="",
        reason="no lever object",
    )
    level = _ProgrammableLevel(
        name="lvl", scripted_results=[bare_leveraged, _matched()]
    )
    cascade.register(level)

    result = cascade.attempt(_situation("q"))
    assert result.status == CascadeStatus.MATCHED


# ── No leverage = original cascade behavior preserved ───────────────────────


def test_no_lever_cascade_behavior_unchanged():
    """Regression check: a walker with no lever-surfacing levels behaves
    exactly like the pre-lever-interrupt cascade."""
    cortex = _make_cortex(search_results=[{"id": "direct"}])
    cascade = ExperimentCascade(cortex)
    cascade.register(Level0ExactRecall())
    cascade.register(Level5LLMEscalationStub())

    result = cascade.attempt(_situation())
    assert result.status == CascadeStatus.MATCHED
    assert result.level_name == "level_0_exact_recall"


# ── CascadeResult.lever field ────────────────────────────────────────────────


def test_cascade_result_lever_field_defaults_none():
    r = CascadeResult(status=CascadeStatus.MATCHED, level_name="x")
    assert r.lever is None


def test_cascade_result_lever_field_stores_lever():
    lever = Lever(anchor_id="X")
    r = CascadeResult(status=CascadeStatus.LEVERAGED, level_name="x", lever=lever)
    assert r.lever is lever
