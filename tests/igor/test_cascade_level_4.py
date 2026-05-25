"""
test_cascade_level_4.py — T-cascade-level-4-past-experiment

Tests for Level4PastExperimentLookup — the concrete replacement for
the level-4 stub. Reuses past Experiment outcomes from
experiment_queue when hypothesis signatures overlap.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.cognition.experiment import (  # noqa: E402
    Experiment,
    ExperimentStatus,
    Hypothesis,
    Observation,
    Outcome,
    Probe,
    ProbeKind,
)
from devices.igor.cognition.experiment_cascade import (  # noqa: E402
    MIN_OVERLAP_TOKENS,
    CascadeSituation,
    CascadeStatus,
    Level4PastExperimentLookup,
    _best_overlap,
    _level4_tokens,
)


def _situation(query: str = "find the goal tree lookup") -> CascadeSituation:
    return CascadeSituation(query=query)


def _make_past_experiment(
    statement: str,
    outcome: Outcome = Outcome.MATCH,
    data: dict | None = None,
) -> Experiment:
    exp = Experiment(
        hypothesis=Hypothesis(statement=statement, source="test", confidence=0.5),
        probe=Probe(kind=ProbeKind.MEMORY_QUERY, target=statement[:20] or "x"),
    )
    exp.advance(ExperimentStatus.RUNNING)
    exp.record_observation(Observation(outcome=outcome, data=data or {"hit": 1}))
    return exp


def _make_cortex(past_experiments: list[Experiment]):
    cortex = MagicMock()
    conn = MagicMock()
    cortex._db.return_value.__enter__.return_value = conn
    cortex._db.return_value.__exit__.return_value = False
    conn.fetchall.return_value = [(e.to_json(),) for e in past_experiments]
    return cortex


# ── _level4_tokens ───────────────────────────────────────────────────────────


def test_tokens_extract_content_words():
    t = _level4_tokens("find the goal tree lookup")
    assert "find" in t
    assert "goal" in t
    assert "tree" in t
    # short words filtered (len < 3)


def test_tokens_empty_string():
    assert _level4_tokens("") == set()


def test_tokens_lowercase():
    t = _level4_tokens("FIND Goal")
    assert "find" in t
    assert "goal" in t
    assert "FIND" not in t


# ── _best_overlap ────────────────────────────────────────────────────────────


def test_best_overlap_returns_none_when_empty():
    assert _best_overlap([], {"find", "goal"}) is None


def test_best_overlap_skips_no_observation():
    exp = Experiment(
        hypothesis=Hypothesis(statement="find goal tree", source="test"),
        probe=Probe(kind=ProbeKind.MEMORY_QUERY, target="x"),
    )
    # no observation attached — still PROPOSED
    assert _best_overlap([exp], {"find", "goal", "tree"}) is None


def test_best_overlap_skips_mismatch():
    exp = _make_past_experiment("find the goal tree", outcome=Outcome.MISMATCH)
    assert _best_overlap([exp], {"find", "goal", "tree"}) is None


def test_best_overlap_skips_inconclusive():
    exp = _make_past_experiment("find the goal tree", outcome=Outcome.INCONCLUSIVE)
    assert _best_overlap([exp], {"find", "goal", "tree"}) is None


def test_best_overlap_accepts_partial():
    exp = _make_past_experiment("find the goal tree", outcome=Outcome.PARTIAL)
    result = _best_overlap([exp], {"find", "goal", "tree"})
    assert result is not None
    assert result[0] is exp


def test_best_overlap_requires_min_tokens():
    """Overlap below MIN_OVERLAP_TOKENS=2 is rejected."""
    exp = _make_past_experiment("find the goal tree")
    # Only 1 overlapping token
    result = _best_overlap([exp], {"goal", "unrelated", "words"})
    assert result is None


def test_best_overlap_picks_highest_overlap():
    exp_low = _make_past_experiment("find the goal")  # overlap=2 with query
    exp_high = _make_past_experiment("find the goal tree lookup")  # overlap=4
    query_tokens = {"find", "goal", "tree", "lookup"}
    result = _best_overlap([exp_low, exp_high], query_tokens)
    assert result is not None
    assert result[0] is exp_high
    assert result[1] == 4


# ── Level4PastExperimentLookup ───────────────────────────────────────────────


def test_level4_matches_past_experiment():
    past = _make_past_experiment(
        "find the goal tree lookup",
        data={"answer": "PR_GOAL_ASPIRATIONAL_SUCK_LESS"},
    )
    cortex = _make_cortex([past])
    level = Level4PastExperimentLookup()

    result = level.try_probe(cortex, _situation("find the goal tree"))
    assert result.status == CascadeStatus.MATCHED
    assert result.level_name == "level_4_past_experiment_lookup"
    assert result.data == {"answer": "PR_GOAL_ASPIRATIONAL_SUCK_LESS"}
    assert past.experiment_id in result.reason


def test_level4_exhausted_when_no_matches():
    cortex = _make_cortex([])
    level = Level4PastExperimentLookup()

    result = level.try_probe(cortex, _situation())
    assert result.status == CascadeStatus.EXHAUSTED
    assert result.experiment.observation.outcome == Outcome.INCONCLUSIVE


def test_level4_exhausted_when_overlap_insufficient():
    past = _make_past_experiment("completely different subject matter")
    cortex = _make_cortex([past])
    level = Level4PastExperimentLookup()

    result = level.try_probe(cortex, _situation("find the goal tree"))
    assert result.status == CascadeStatus.EXHAUSTED


def test_level4_exhausted_when_query_empty():
    cortex = _make_cortex([])
    level = Level4PastExperimentLookup()
    result = level.try_probe(cortex, _situation(""))
    assert result.status == CascadeStatus.EXHAUSTED
    assert "no content tokens" in result.reason


def test_level4_exhausted_on_db_error():
    cortex = MagicMock()
    cortex._db.side_effect = RuntimeError("db down")
    level = Level4PastExperimentLookup()

    result = level.try_probe(cortex, _situation())
    assert result.status == CascadeStatus.EXHAUSTED
    assert "RuntimeError" in result.reason


def test_level4_skips_mismatch_past_experiments():
    past = _make_past_experiment("find the goal tree lookup", outcome=Outcome.MISMATCH)
    cortex = _make_cortex([past])
    level = Level4PastExperimentLookup()

    result = level.try_probe(cortex, _situation("find the goal tree"))
    assert result.status == CascadeStatus.EXHAUSTED


def test_level4_reuses_observation_data():
    past = _make_past_experiment(
        "find widget in graph",
        data={"widget_id": "W_1", "location": "shelf_3"},
    )
    cortex = _make_cortex([past])
    level = Level4PastExperimentLookup()

    result = level.try_probe(cortex, _situation("find widget graph"))
    assert result.status == CascadeStatus.MATCHED
    assert result.data["widget_id"] == "W_1"
    assert result.data["location"] == "shelf_3"


def test_level4_experiment_advances_to_observed():
    past = _make_past_experiment("find widget graph")
    cortex = _make_cortex([past])
    level = Level4PastExperimentLookup()

    result = level.try_probe(cortex, _situation("find widget graph"))
    assert result.experiment.status == ExperimentStatus.OBSERVED
    assert result.experiment.observation is not None


def test_min_overlap_tokens_default():
    assert MIN_OVERLAP_TOKENS == 2


# ── Integration via default cascade ─────────────────────────────────────────


def test_default_cascade_level_4_is_concrete_not_stub():
    from devices.igor.cognition.experiment_cascade import build_default_cascade

    cortex = MagicMock()
    cortex.search.return_value = []
    cortex.twm_push.return_value = 1
    cascade = build_default_cascade(cortex)

    # Find the level 4 instance
    level_4 = next(
        lv for lv in cascade._levels if lv.name == "level_4_past_experiment_lookup"
    )
    assert isinstance(level_4, Level4PastExperimentLookup)
