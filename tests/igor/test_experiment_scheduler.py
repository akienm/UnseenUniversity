"""
test_experiment_scheduler.py — T-experiment-primitive-scheduler (sub-slice of #456)

Unit tests for the scheduler/runner that consumes Experiment objects.
Mocks cortex via the `_db()` context-manager pattern (matches
test_instance_tracker.py).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.cognition.experiment import (  # noqa: E402
    Experiment,
    ExperimentStatus,
    Hypothesis,
    Observation,
    Outcome,
    Probe,
    ProbeKind,
)
from wild_igor.igor.cognition.experiment_scheduler import (  # noqa: E402
    DEFAULT_TIMEOUT_SEC,
    SAFE_PROBE_KINDS,
    ExperimentScheduler,
)

# ── Mock cortex helper ───────────────────────────────────────────────────────


def _make_mock_cortex(fetchone_rows=None, search_results=None):
    """Mock cortex with _db() context manager and search() method.

    fetchone_rows: list of rows that successive fetchone() calls will return,
                   or a single row, or None.
    """
    cortex = MagicMock()
    conn = MagicMock()
    cortex._db.return_value.__enter__.return_value = conn
    cortex._db.return_value.__exit__.return_value = False

    if fetchone_rows is None:
        conn.fetchone.return_value = None
        conn.fetchall.return_value = []
    elif isinstance(fetchone_rows, list):
        conn.fetchone.side_effect = fetchone_rows + [None]
        conn.fetchall.return_value = []
    else:
        conn.fetchone.return_value = fetchone_rows
        conn.fetchall.return_value = [fetchone_rows]

    cortex.search.return_value = search_results or []
    cortex.twm_push.return_value = 1
    return cortex, conn


def _proposed_experiment(
    kind: ProbeKind = ProbeKind.MEMORY_QUERY,
    target: str = "word graph",
    payload: dict | None = None,
) -> Experiment:
    return Experiment(
        hypothesis=Hypothesis(
            statement="searching for X should surface relevant memories",
            source="substrate",
            confidence=0.5,
        ),
        probe=Probe(
            kind=kind,
            target=target,
            payload=payload or {},
            expected_shape="at least one memory",
        ),
    )


# ── Enqueue ──────────────────────────────────────────────────────────────────


def test_enqueue_inserts_row():
    cortex, conn = _make_mock_cortex()
    scheduler = ExperimentScheduler(cortex)
    exp = _proposed_experiment()

    eid = scheduler.enqueue(exp)

    assert eid == exp.experiment_id
    # An INSERT was executed
    insert_calls = [
        call
        for call in conn.execute.call_args_list
        if "INSERT INTO experiment_queue" in call.args[0]
    ]
    assert len(insert_calls) == 1


def test_enqueue_rejects_non_proposed():
    cortex, _ = _make_mock_cortex()
    scheduler = ExperimentScheduler(cortex)
    exp = _proposed_experiment()
    exp.advance(ExperimentStatus.RUNNING)

    with pytest.raises(ValueError, match="requires status=PROPOSED"):
        scheduler.enqueue(exp)


# ── Tick on empty queue ──────────────────────────────────────────────────────


def test_tick_empty_queue_returns_none():
    cortex, _ = _make_mock_cortex()
    scheduler = ExperimentScheduler(cortex)
    assert scheduler.tick() is None


# ── Memory query happy path ──────────────────────────────────────────────────


def test_run_one_memory_query_match():
    cortex, conn = _make_mock_cortex(search_results=[{"id": "mem_1"}, {"id": "mem_2"}])
    scheduler = ExperimentScheduler(cortex)
    exp = _proposed_experiment(
        kind=ProbeKind.MEMORY_QUERY,
        target="word graph",
        payload={"limit": 5},
    )

    result = scheduler.run_one(exp)

    assert result.status == ExperimentStatus.OBSERVED
    assert result.observation is not None
    assert result.observation.outcome == Outcome.MATCH
    assert result.observation.data["result_count"] == 2
    cortex.search.assert_called_once()
    assert cortex.twm_push.called


def test_run_one_memory_query_inconclusive_when_no_results():
    cortex, _ = _make_mock_cortex(search_results=[])
    scheduler = ExperimentScheduler(cortex)
    exp = _proposed_experiment()

    result = scheduler.run_one(exp)
    assert result.observation.outcome == Outcome.INCONCLUSIVE
    assert result.observation.data["result_count"] == 0


# ── Probe failure → INCONCLUSIVE, not crash ──────────────────────────────────


def test_run_one_probe_raises_records_inconclusive():
    cortex, _ = _make_mock_cortex()
    cortex.search.side_effect = RuntimeError("db down")
    scheduler = ExperimentScheduler(cortex)
    exp = _proposed_experiment()

    result = scheduler.run_one(exp)
    assert result.status == ExperimentStatus.OBSERVED
    assert result.observation.outcome == Outcome.INCONCLUSIVE
    assert "RuntimeError" in result.observation.data["error"]


# ── Whitelist enforcement ────────────────────────────────────────────────────


def test_run_one_unsupported_probe_kind_aborts():
    """If a probe kind were removed from SAFE_PROBE_KINDS, it would abort."""
    cortex, _ = _make_mock_cortex()
    scheduler = ExperimentScheduler(cortex)
    exp = _proposed_experiment(
        kind=ProbeKind.MEMORY_QUERY,
        target="test",
    )
    # Temporarily shrink whitelist to force an abort
    import wild_igor.igor.cognition.experiment_scheduler as _mod

    orig = _mod.SAFE_PROBE_KINDS
    _mod.SAFE_PROBE_KINDS = frozenset()
    try:
        result = scheduler.run_one(exp)
        assert result.status == ExperimentStatus.ABORTED
        assert cortex.twm_push.called
    finally:
        _mod.SAFE_PROBE_KINDS = orig


def test_safe_probe_kinds_covers_all():
    """All defined ProbeKinds should be in SAFE_PROBE_KINDS."""
    for kind in ProbeKind:
        assert kind in SAFE_PROBE_KINDS


# ── Tool call dispatch ───────────────────────────────────────────────────────


def test_run_one_tool_call_unknown_tool_mismatches():
    cortex, _ = _make_mock_cortex()
    scheduler = ExperimentScheduler(cortex)
    exp = _proposed_experiment(
        kind=ProbeKind.TOOL_CALL,
        target="this_tool_does_not_exist_anywhere",
    )

    with patch(
        "lab.utility_closet.registry.registry.get",
        return_value=None,
    ):
        result = scheduler.run_one(exp)

    assert result.status == ExperimentStatus.OBSERVED
    assert result.observation.outcome == Outcome.MISMATCH
    assert result.observation.data["error"] == "unknown_tool"


def test_run_one_tool_call_success():
    cortex, _ = _make_mock_cortex()
    scheduler = ExperimentScheduler(cortex)
    exp = _proposed_experiment(
        kind=ProbeKind.TOOL_CALL,
        target="dummy_tool",
        payload={"x": 1},
    )

    fake_tool = MagicMock()
    with patch(
        "lab.utility_closet.registry.registry.get",
        return_value=fake_tool,
    ), patch(
        "lab.utility_closet.registry.registry.execute",
        return_value="ok: did the thing",
    ):
        result = scheduler.run_one(exp)

    assert result.status == ExperimentStatus.OBSERVED
    assert result.observation.outcome == Outcome.MATCH


def test_run_one_tool_call_error_string_is_mismatch():
    cortex, _ = _make_mock_cortex()
    scheduler = ExperimentScheduler(cortex)
    exp = _proposed_experiment(
        kind=ProbeKind.TOOL_CALL,
        target="failing_tool",
    )

    fake_tool = MagicMock()
    with patch(
        "lab.utility_closet.registry.registry.get",
        return_value=fake_tool,
    ), patch(
        "lab.utility_closet.registry.registry.execute",
        return_value="Error: tool blew up",
    ):
        result = scheduler.run_one(exp)

    assert result.observation.outcome == Outcome.MISMATCH


# ── DB query dispatch ────────────────────────────────────────────────────────


def test_run_one_db_query_match_when_rows_returned():
    cortex, conn = _make_mock_cortex()
    # Probe execution path: conn.fetchall returns two rows for the SQL probe.
    conn.fetchall.return_value = [("a",), ("b",)]
    scheduler = ExperimentScheduler(cortex)
    exp = _proposed_experiment(
        kind=ProbeKind.DB_QUERY,
        target="SELECT id FROM memories LIMIT 2",
    )

    result = scheduler.run_one(exp)
    assert result.observation.outcome == Outcome.MATCH
    assert result.observation.data["row_count"] == 2


# ── Timeout abort ────────────────────────────────────────────────────────────


def test_timeout_aborts_experiment():
    """A probe slower than timeout_sec → ABORTED with reason."""
    import time as _time

    cortex, _ = _make_mock_cortex()

    def slow_search(*args, **kwargs):
        _time.sleep(0.05)
        return [{"id": "mem_1"}]

    cortex.search.side_effect = slow_search
    # Microscopic timeout to guarantee abort
    scheduler = ExperimentScheduler(cortex, timeout_sec=0.01)
    exp = _proposed_experiment()

    result = scheduler.run_one(exp)
    assert result.status == ExperimentStatus.ABORTED


# ── TWM push carries cp1_provisional ─────────────────────────────────────────


def test_outcome_push_carries_cp1_provisional_flag():
    cortex, _ = _make_mock_cortex(search_results=[{"id": "x"}])
    scheduler = ExperimentScheduler(cortex)
    exp = _proposed_experiment()

    scheduler.run_one(exp)

    push_call = cortex.twm_push.call_args
    metadata = push_call.kwargs["metadata"]
    assert metadata["cp1_provisional"] is True
    assert metadata["type"] == "experiment_outcome"
    assert metadata["experiment_id"] == exp.experiment_id


def test_twm_push_failure_does_not_break_run():
    cortex, _ = _make_mock_cortex(search_results=[{"id": "x"}])
    cortex.twm_push.side_effect = RuntimeError("twm down")
    scheduler = ExperimentScheduler(cortex)
    exp = _proposed_experiment()

    result = scheduler.run_one(exp)
    # The experiment still completed cleanly even though twm push failed
    assert result.status == ExperimentStatus.OBSERVED
    assert result.observation.outcome == Outcome.MATCH


# ── Persistence roundtrip via tick() ─────────────────────────────────────────


def test_tick_picks_next_proposed_and_runs_it():
    cortex, conn = _make_mock_cortex(search_results=[{"id": "x"}])
    scheduler = ExperimentScheduler(cortex)

    exp = _proposed_experiment()
    # Simulate the queue returning this experiment as JSON
    conn.fetchone.return_value = (exp.to_json(),)

    result = scheduler.tick()
    assert result is not None
    assert result.experiment_id == exp.experiment_id
    assert result.status == ExperimentStatus.OBSERVED


# ── Default timeout ──────────────────────────────────────────────────────────


def test_default_timeout_is_reasonable():
    """30s default lets memory queries / db queries / tool calls finish."""
    assert DEFAULT_TIMEOUT_SEC == 30.0


# ── Expanded dispatch: HABIT_DRYRUN ──────────────────────────────────────────


def test_habit_dryrun_found():
    cortex, conn = _make_mock_cortex()
    conn.fetchone.return_value = ("habit-123", "do the thing", "{}")
    scheduler = ExperimentScheduler(cortex)
    exp = Experiment(
        hypothesis=Hypothesis(statement="habit X exists", source="test"),
        probe=Probe(kind=ProbeKind.HABIT_DRYRUN, target="habit-123"),
    )
    exp.advance(ExperimentStatus.RUNNING)
    from wild_igor.igor.cognition.experiment_scheduler import _dispatch_habit_dryrun

    obs = _dispatch_habit_dryrun(cortex, exp)
    assert obs.outcome == Outcome.MATCH
    assert obs.data["habit_id"] == "habit-123"


def test_habit_dryrun_not_found():
    cortex, conn = _make_mock_cortex()
    conn.fetchone.return_value = None
    exp = Experiment(
        hypothesis=Hypothesis(statement="habit X exists", source="test"),
        probe=Probe(kind=ProbeKind.HABIT_DRYRUN, target="nonexistent"),
    )
    exp.advance(ExperimentStatus.RUNNING)
    from wild_igor.igor.cognition.experiment_scheduler import _dispatch_habit_dryrun

    obs = _dispatch_habit_dryrun(cortex, exp)
    assert obs.outcome == Outcome.MISMATCH


# ── Expanded dispatch: CHANNEL_SEND ──────────────────────────────────────────


def test_channel_send_success():
    cortex, _ = _make_mock_cortex()
    exp = Experiment(
        hypothesis=Hypothesis(statement="channel works", source="test"),
        probe=Probe(kind=ProbeKind.CHANNEL_SEND, target="test message"),
    )
    exp.advance(ExperimentStatus.RUNNING)
    from wild_igor.igor.cognition.experiment_scheduler import _dispatch_channel_send

    with patch("wild_igor.igor.tools.channel_post.post_to_channel") as mock_post:
        obs = _dispatch_channel_send(cortex, exp)
    assert obs.outcome == Outcome.MATCH
    mock_post.assert_called_once()


def test_channel_send_failure():
    cortex, _ = _make_mock_cortex()
    exp = Experiment(
        hypothesis=Hypothesis(statement="channel works", source="test"),
        probe=Probe(kind=ProbeKind.CHANNEL_SEND, target="test message"),
    )
    exp.advance(ExperimentStatus.RUNNING)
    from wild_igor.igor.cognition.experiment_scheduler import _dispatch_channel_send

    with patch(
        "wild_igor.igor.tools.channel_post.post_to_channel",
        side_effect=ConnectionError("down"),
    ):
        obs = _dispatch_channel_send(cortex, exp)
    assert obs.outcome == Outcome.INCONCLUSIVE


# ── Expanded dispatch: SIM_TURN ──────────────────────────────────────────────


def test_sim_turn_cascade_match():
    cortex, _ = _make_mock_cortex(search_results=[{"id": "x"}])
    exp = Experiment(
        hypothesis=Hypothesis(statement="cascade can handle this", source="test"),
        probe=Probe(
            kind=ProbeKind.SIM_TURN,
            target="test query",
            payload={"query": "test query"},
        ),
    )
    exp.advance(ExperimentStatus.RUNNING)
    from wild_igor.igor.cognition.experiment_scheduler import _dispatch_sim_turn

    from wild_igor.igor.cognition.experiment_cascade import (
        CascadeResult,
        CascadeStatus,
    )

    mock_cascade = MagicMock()
    mock_cascade.attempt.return_value = CascadeResult(
        status=CascadeStatus.MATCHED,
        level_name="test_level",
        data=["result"],
        reason="matched",
    )
    with patch(
        "wild_igor.igor.cognition.experiment_cascade.build_default_cascade",
        return_value=mock_cascade,
    ):
        obs = _dispatch_sim_turn(cortex, exp)
    assert obs.outcome == Outcome.MATCH
    assert obs.data["cascade_status"] == "matched"


# ── All probe kinds now safe ─────────────────────────────────────────────────


def test_all_probe_kinds_are_safe():
    for kind in ProbeKind:
        assert kind in SAFE_PROBE_KINDS, f"{kind} not in SAFE_PROBE_KINDS"
