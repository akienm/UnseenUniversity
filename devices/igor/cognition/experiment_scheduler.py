"""
experiment_scheduler.py — T-experiment-primitive-scheduler (sub-slice of #456)

The scheduler/runner half of the experiment primitive. The schema layer
(experiment.py) defines the data model; this module persists Experiments
in a queue, picks the next ready one, dispatches its Probe, records the
Observation, and pushes the OBSERVED outcome to TWM.

## Axiomatic grounding (CP1-CP6, future-self-proud test)

- **CP1** — every probe outcome is representable, including INCONCLUSIVE.
  The scheduler never "succeeds by default" when a probe times out or
  raises; that becomes a real Observation with outcome=INCONCLUSIVE.
- **CP2** — failures are first-class. A MISMATCH outcome is fully valid
  scheduler output, captured into the queue lifecycle and pushed to TWM
  the same way a MATCH would be.
- **CP3** — every dispatched Probe must specify what it expects via
  `expected_shape`. The scheduler does not enforce semantic validation
  here — that's the cp-check sub-slice — but it threads expected_shape
  through to the Observation notes so the audit layer can grade later.
- **CP6** — only whitelisted ProbeKinds are dispatched in MVP. Anything
  else aborts with a reason. The TWM push carries cp1_provisional=True
  so downstream consumers know an OBSERVED experiment is data, not a
  commitment.

## Out of scope (intentional, picked up by later sub-slices)

- Engram updates from outcomes — that's T-experiment-primitive-outcome-feedback
- Mode-aware scheduling (AWAKE vs BACKGROUND vs SLEEP) — pick simplest
- Risk-tier gating to Akien approval — flag in code, don't implement
- Cockpit integration / cp-check — separate sub-slices
"""

from __future__ import annotations
from ..igor_base import IgorBase

import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable, Optional

from .experiment import (
    Experiment,
    ExperimentStatus,
    Observation,
    Outcome,
    ProbeKind,
)
from ..igor_base import get_logger

if TYPE_CHECKING:
    from ..memory.cortex import Cortex

logger = get_logger(__name__)


# Probe kinds the MVP scheduler will actually run. Anything else aborts
# with reason="probe_kind_not_implemented" (CP1: representable, not silent).
SAFE_PROBE_KINDS: frozenset[ProbeKind] = frozenset(
    {
        ProbeKind.MEMORY_QUERY,
        ProbeKind.DB_QUERY,
        ProbeKind.TOOL_CALL,
        ProbeKind.HABIT_DRYRUN,
        ProbeKind.CHANNEL_SEND,
        ProbeKind.SIM_TURN,
    }
)

DEFAULT_TIMEOUT_SEC: float = 30.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Persistence helpers ──────────────────────────────────────────────────────


def _enqueue_row(cortex: "Cortex", experiment: Experiment) -> None:
    with cortex._db() as conn:
        conn.execute(
            "INSERT INTO experiment_queue "
            "(experiment_id, status, enqueued_at, experiment_json) "
            "VALUES (%s, %s, %s, %s)",
            (
                experiment.experiment_id,
                experiment.status.value,
                _now_iso(),
                experiment.to_json(),
            ),
        )


def _update_row(
    cortex: "Cortex",
    experiment: Experiment,
    started_at: Optional[str] = None,
    completed_at: Optional[str] = None,
) -> None:
    sets = ["status = %s", "experiment_json = %s"]
    args: list[Any] = [experiment.status.value, experiment.to_json()]
    if started_at is not None:
        sets.append("started_at = %s")
        args.append(started_at)
    if completed_at is not None:
        sets.append("completed_at = %s")
        args.append(completed_at)
    args.append(experiment.experiment_id)
    with cortex._db() as conn:
        conn.execute(
            f"UPDATE experiment_queue SET {', '.join(sets)} WHERE experiment_id = %s",
            tuple(args),
        )


def _next_proposed(cortex: "Cortex") -> Optional[Experiment]:
    with cortex._db() as conn:
        conn.execute(
            "SELECT experiment_json FROM experiment_queue "
            "WHERE status = %s ORDER BY enqueued_at LIMIT 1",
            (ExperimentStatus.PROPOSED.value,),
        )
        row = conn.fetchone()
    if not row:
        return None
    return Experiment.from_json(row[0])


def recent_completed(cortex: "Cortex", limit: int = 5) -> list[dict]:
    """Return recent OBSERVED/UPDATED experiments as dicts for reasoning context."""
    with cortex._db() as conn:
        conn.execute(
            "SELECT experiment_json FROM experiment_queue "
            "WHERE status IN (%s, %s) "
            "ORDER BY completed_at DESC NULLS LAST LIMIT %s",
            (ExperimentStatus.OBSERVED.value, ExperimentStatus.UPDATED.value, limit),
        )
        rows = conn.fetchall()
    results = []
    for row in rows:
        try:
            exp = Experiment.from_json(row[0])
            d = {
                "hypothesis": exp.hypothesis.statement,
                "status": exp.status.value,
                "outcome": (
                    exp.observation.outcome.value if exp.observation else "pending"
                ),
            }
            if exp.update and exp.update.reason:
                d["update_reason"] = exp.update.reason
            results.append(d)
        except Exception:
            continue
    return results


# ── Probe dispatch ───────────────────────────────────────────────────────────


def _dispatch_memory_query(cortex: "Cortex", experiment: Experiment) -> Observation:
    probe = experiment.probe
    query = probe.payload.get("query") or probe.target
    limit = int(probe.payload.get("limit", 5))
    t0 = time.perf_counter()
    results = cortex.search(query, limit=limit)
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    n = len(results) if results else 0
    outcome = Outcome.MATCH if n > 0 else Outcome.INCONCLUSIVE
    return Observation(
        outcome=outcome,
        data={"result_count": n, "query": query},
        cost={"latency_ms": elapsed_ms},
        notes=f"memory_query returned {n} result(s); expected: {probe.expected_shape!r}",
    )


def _dispatch_db_query(cortex: "Cortex", experiment: Experiment) -> Observation:
    probe = experiment.probe
    sql = probe.target
    params = probe.payload.get("params", ())
    if not isinstance(params, (list, tuple)):
        params = (params,)
    t0 = time.perf_counter()
    with cortex._db() as conn:
        conn.execute(sql, tuple(params))
        rows = conn.fetchall()
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    n = len(rows) if rows else 0
    outcome = Outcome.MATCH if n > 0 else Outcome.INCONCLUSIVE
    return Observation(
        outcome=outcome,
        data={"row_count": n},
        cost={"latency_ms": elapsed_ms},
        notes=f"db_query returned {n} row(s); expected: {probe.expected_shape!r}",
    )


def _dispatch_tool_call(cortex: "Cortex", experiment: Experiment) -> Observation:
    from ..tools.registry import registry

    probe = experiment.probe
    tool = registry.get(probe.target)
    if tool is None:
        return Observation(
            outcome=Outcome.MISMATCH,
            data={"error": "unknown_tool", "tool_name": probe.target},
            notes=f"tool {probe.target!r} not in registry",
        )
    t0 = time.perf_counter()
    result = registry.execute(probe.target, dict(probe.payload))
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    is_error = isinstance(result, str) and result.startswith("Error")
    outcome = Outcome.MISMATCH if is_error else Outcome.MATCH
    return Observation(
        outcome=outcome,
        data={"result_repr": str(result)[:500]},
        cost={"latency_ms": elapsed_ms},
        notes=f"tool_call {probe.target}; expected: {probe.expected_shape!r}",
    )


def _dispatch_habit_dryrun(cortex: "Cortex", experiment: Experiment) -> Observation:
    probe = experiment.probe
    habit_id = probe.target
    t0 = time.perf_counter()
    try:
        with cortex._db() as conn:
            conn.execute(
                "SELECT id, content, metadata FROM memories "
                "WHERE memory_type = 'PROCEDURAL' AND id = %s",
                (habit_id,),
            )
            row = conn.fetchone()
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if not row:
            return Observation(
                outcome=Outcome.MISMATCH,
                data={"error": "habit_not_found", "habit_id": habit_id},
                cost={"latency_ms": elapsed_ms},
                notes=f"habit {habit_id!r} not in DB",
            )
        return Observation(
            outcome=Outcome.MATCH,
            data={"habit_id": habit_id, "content_preview": str(row[1])[:200]},
            cost={"latency_ms": elapsed_ms},
            notes=f"habit_dryrun found {habit_id}; expected: {probe.expected_shape!r}",
        )
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return Observation(
            outcome=Outcome.INCONCLUSIVE,
            data={"error": str(e)[:200]},
            cost={"latency_ms": elapsed_ms},
            notes=f"habit_dryrun raised: {e}",
        )


def _dispatch_channel_send(cortex: "Cortex", experiment: Experiment) -> Observation:
    probe = experiment.probe
    channel = probe.payload.get("channel", "shared")
    message = probe.target
    t0 = time.perf_counter()
    try:
        from ..tools.channel_post import post_to_channel

        post_to_channel(message, channel=channel)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return Observation(
            outcome=Outcome.MATCH,
            data={"channel": channel, "message_preview": message[:200]},
            cost={"latency_ms": elapsed_ms},
            notes=f"channel_send to {channel}; expected: {probe.expected_shape!r}",
        )
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return Observation(
            outcome=Outcome.INCONCLUSIVE,
            data={"error": str(e)[:200]},
            cost={"latency_ms": elapsed_ms},
            notes=f"channel_send raised: {e}",
        )


def _dispatch_sim_turn(cortex: "Cortex", experiment: Experiment) -> Observation:
    """Simulate a turn through the cascade without committing.

    Uses the same CascadeSituation→ExperimentCascade path but only
    observes whether the cascade matches, escalates, or exhausts.
    """
    probe = experiment.probe
    query = probe.payload.get("query") or probe.target
    t0 = time.perf_counter()
    try:
        from .experiment_cascade import (
            CascadeSituation,
            CascadeStatus,
            build_default_cascade,
        )

        situation = CascadeSituation(query=query, stakes=0.1)
        cascade = build_default_cascade(cortex)
        result = cascade.attempt(situation)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        if result.status == CascadeStatus.MATCHED:
            outcome = Outcome.MATCH
        elif result.status == CascadeStatus.ESCALATE:
            outcome = Outcome.PARTIAL
        else:
            outcome = Outcome.INCONCLUSIVE
        return Observation(
            outcome=outcome,
            data={
                "cascade_status": result.status.value,
                "level_name": result.level_name,
                "reason": result.reason[:200],
            },
            cost={"latency_ms": elapsed_ms},
            notes=f"sim_turn cascade={result.status.value}; expected: {probe.expected_shape!r}",
        )
    except Exception as e:
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return Observation(
            outcome=Outcome.INCONCLUSIVE,
            data={"error": str(e)[:200]},
            cost={"latency_ms": elapsed_ms},
            notes=f"sim_turn raised: {e}",
        )


_DISPATCH: dict[ProbeKind, Callable[["Cortex", Experiment], Observation]] = {
    ProbeKind.MEMORY_QUERY: _dispatch_memory_query,
    ProbeKind.DB_QUERY: _dispatch_db_query,
    ProbeKind.TOOL_CALL: _dispatch_tool_call,
    ProbeKind.HABIT_DRYRUN: _dispatch_habit_dryrun,
    ProbeKind.CHANNEL_SEND: _dispatch_channel_send,
    ProbeKind.SIM_TURN: _dispatch_sim_turn,
}


# ── Scheduler ────────────────────────────────────────────────────────────────


class ExperimentScheduler(IgorBase):
    """In-process scheduler with Postgres-backed queue.

    Single-tick model: `tick()` picks the oldest PROPOSED experiment and
    runs it through PROPOSED → RUNNING → OBSERVED (or ABORTED). Callers
    drive tick() from whatever cadence makes sense for their mode (NE
    cycle, idle slot, sleep pass).
    """

    def __init__(
        self,
        cortex: "Cortex",
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        twm_salience: float = 0.6,
    ) -> None:
        self.cortex = cortex
        self.timeout_sec = timeout_sec
        self.twm_salience = twm_salience

    def enqueue(self, experiment: Experiment) -> str:
        if experiment.status != ExperimentStatus.PROPOSED:
            raise ValueError(
                f"enqueue requires status=PROPOSED, got {experiment.status.value}"
            )
        _enqueue_row(self.cortex, experiment)
        logger.info("experiment_scheduler enqueued %s", experiment.experiment_id)
        return experiment.experiment_id

    def tick(self) -> Optional[Experiment]:
        """Run one experiment to terminal state. Returns the experiment, or None
        if the queue is empty.
        """
        experiment = _next_proposed(self.cortex)
        if experiment is None:
            return None
        return self.run_one(experiment)

    def run_one(self, experiment: Experiment) -> Experiment:
        # Whitelist enforcement (CP6)
        if experiment.probe.kind not in SAFE_PROBE_KINDS:
            experiment.abort(
                reason=f"probe_kind_not_implemented:{experiment.probe.kind.value}"
            )
            _update_row(self.cortex, experiment, completed_at=_now_iso())
            self._push_outcome(experiment)
            return experiment

        started_at = _now_iso()
        try:
            experiment.advance(ExperimentStatus.RUNNING)
        except ValueError as e:
            logger.warning("experiment_scheduler advance failed: %s", e)
            return experiment
        _update_row(self.cortex, experiment, started_at=started_at)

        dispatcher = _DISPATCH[experiment.probe.kind]
        t0 = time.perf_counter()
        try:
            observation = dispatcher(self.cortex, experiment)
        except Exception as e:
            logger.warning(
                "experiment_scheduler probe raised: %s — recording as INCONCLUSIVE",
                e,
            )
            observation = Observation(
                outcome=Outcome.INCONCLUSIVE,
                data={"error": type(e).__name__, "detail": str(e)[:500]},
                notes=f"probe raised {type(e).__name__}",
            )
        elapsed = time.perf_counter() - t0
        if elapsed > self.timeout_sec:
            experiment.abort(reason=f"timeout:{elapsed:.1f}s>{self.timeout_sec:.1f}s")
            _update_row(self.cortex, experiment, completed_at=_now_iso())
            self._push_outcome(experiment)
            return experiment

        experiment.record_observation(observation)
        _update_row(self.cortex, experiment, completed_at=_now_iso())
        self._push_outcome(experiment)
        return experiment

    def _push_outcome(self, experiment: Experiment) -> None:
        try:
            outcome_str = (
                experiment.observation.outcome.value
                if experiment.observation
                else experiment.status.value
            )
            content = (
                f"EXPERIMENT_OUTCOME {experiment.experiment_id} "
                f"hypothesis={experiment.hypothesis.statement!r} "
                f"outcome={outcome_str}"
            )
            metadata = {
                "type": "experiment_outcome",
                "experiment_id": experiment.experiment_id,
                "outcome": outcome_str,
                "hypothesis_source": experiment.hypothesis.source,
                "probe_kind": experiment.probe.kind.value,
                "cp1_provisional": True,
            }
            self.cortex.twm_push(
                source="experiment_scheduler",
                content_csb=content,
                salience=self.twm_salience,
                metadata=metadata,
                category="experiment_outcome",
            )
        except Exception as e:
            logger.warning("experiment_scheduler twm_push failed: %s", e)

    def queue_summary(self) -> dict[str, int]:
        with self.cortex._db() as conn:
            conn.execute(
                "SELECT status, COUNT(*) FROM experiment_queue GROUP BY status",
                (),
            )
            rows = conn.fetchall()
        return {row[0]: int(row[1]) for row in (rows or [])}
