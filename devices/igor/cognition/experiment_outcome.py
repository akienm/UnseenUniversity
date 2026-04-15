"""
experiment_outcome.py — T-experiment-primitive-outcome-feedback (sub-slice of #456)

Bridges OBSERVED experiments into engram changes. Where the scheduler
runs probes and records observations, this module reads the observation
and applies the *learning* — Hebbian edge strengthening, memory
accretion, inhibitor adjustment, goal state transitions.

## Axiomatic grounding

- **CP2** — every Outcome (MATCH, MISMATCH, PARTIAL, INCONCLUSIVE)
  produces an Update. Failure is learning, not an error to suppress.
- **CP3** — Update.reason is required and explains *why* each effect
  was applied. The schema enforces non-empty reason.
- **CP6** — engram changes only flow through Update. This module is
  the only place where scheduler outputs become persistent state
  changes; direct mutation from probe handlers is forbidden.

## Wiring

Scheduler.tick() leaves experiments in OBSERVED. This module's
`apply_outcome()` runs as a separate pass (different cadence — could
be background, sleep consolidation, idle). After apply_outcome, the
experiment is UPDATED and the engram changes are live.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from .experiment import (
    Experiment,
    ExperimentStatus,
    Outcome,
    ProbeKind,
    Update,
)

if TYPE_CHECKING:
    from ..memory.cortex import Cortex

logger = logging.getLogger(__name__)


HEBBIAN_DELTA_MATCH: float = 0.05
INHIBITOR_DELTA_TOOL_MISMATCH: float = 0.03


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Update derivation (pure — no I/O, easy to unit-test) ─────────────────────


def derive_update(experiment: Experiment) -> Update:
    """Inspect the experiment's observation and produce an Update describing
    what should change. Pure: does not touch the cortex.
    """
    obs = experiment.observation
    if obs is None:
        raise ValueError(
            "derive_update requires an Observation (experiment must be OBSERVED)"
        )

    update = Update()
    outcome = obs.outcome
    hyp = experiment.hypothesis
    probe = experiment.probe

    reason_parts: list[str] = [
        f"observation outcome={outcome.value}",
        f"hypothesis source={hyp.source}",
        f"probe kind={probe.kind.value} target={probe.target}",
    ]

    # --- Hebbian: MATCH strengthens the (source → probe_target) edge -------
    if outcome == Outcome.MATCH:
        update.trail_edge_changes.append(
            {
                "from": hyp.source,
                "to": probe.target,
                "delta": HEBBIAN_DELTA_MATCH,
                "kind": "hebbian_match",
            }
        )
        reason_parts.append(
            f"strengthened ({hyp.source}→{probe.target}) by {HEBBIAN_DELTA_MATCH:+.2f}"
        )

    # --- Tool-call MISMATCH bumps the cost/risk inhibitor for that tool ---
    if outcome == Outcome.MISMATCH and probe.kind == ProbeKind.TOOL_CALL:
        update.inhibitor_weight_deltas[f"tool:{probe.target}"] = (
            INHIBITOR_DELTA_TOOL_MISMATCH
        )
        reason_parts.append(
            f"bumped tool inhibitor for {probe.target} by "
            f"{INHIBITOR_DELTA_TOOL_MISMATCH:+.2f}"
        )

    # --- Goal state transitions (if hypothesis carries a goal_id) ----------
    goal_id: Optional[str] = (
        hyp.cp_constraints.get("goal_id") if hyp.cp_constraints else None
    )
    if goal_id:
        if outcome == Outcome.MATCH:
            update.goal_state_transitions.append(
                {"goal_id": goal_id, "to": "in_progress", "via": "experiment_match"}
            )
            reason_parts.append(f"goal {goal_id} → in_progress")
        elif outcome == Outcome.MISMATCH:
            update.goal_state_transitions.append(
                {"goal_id": goal_id, "to": "blocked", "via": "experiment_mismatch"}
            )
            reason_parts.append(f"goal {goal_id} → blocked")

    # --- Memory accretion: every outcome leaves a trace ---------------------
    # We add a placeholder id; the apply step replaces it with the real
    # stored memory id. We still record the *intent* to deposit so derive_update
    # is testable without I/O.
    update.memory_accretions.append(f"PENDING:{experiment.experiment_id}")

    update.reason = "; ".join(reason_parts)
    return update


# ── Live application (touches cortex) ────────────────────────────────────────


def _apply_trail_edge(cortex: "Cortex", change: dict[str, Any]) -> None:
    """Best-effort Hebbian edge bump. Silently logs failures (CP1: surface,
    don't crash)."""
    try:
        with cortex._db() as conn:
            conn.execute(
                "INSERT INTO interpretive_edges (from_id, to_id, weight, layer) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT DO NOTHING",
                (change["from"], change["to"], float(change["delta"]), "experiment"),
            )
    except Exception as exc:
        logger.warning("experiment_outcome trail_edge insert failed: %s", exc)


def _deposit_memory(
    cortex: "Cortex", experiment: Experiment, update: Update
) -> Optional[str]:
    """Deposit an EPISODIC memory recording the experiment outcome.
    Returns the new memory id, or None on failure."""
    try:
        from ..memory.models import Memory, MemoryType

        narrative = (
            f"experiment {experiment.experiment_id}: "
            f"hypothesis={experiment.hypothesis.statement!r} "
            f"outcome={experiment.observation.outcome.value} "
            f"reason={update.reason}"
        )
        mem = Memory(
            narrative=narrative,
            memory_type=MemoryType.EPISODIC,
            metadata={
                "type": "experiment_outcome",
                "experiment_id": experiment.experiment_id,
                "outcome": experiment.observation.outcome.value,
                "hypothesis_source": experiment.hypothesis.source,
                "probe_kind": experiment.probe.kind.value,
                "probe_target": experiment.probe.target,
            },
            source="experiment",
            confidence=experiment.hypothesis.confidence,
        )
        stored = cortex.store(mem)
        return stored.id
    except Exception as exc:
        logger.warning("experiment_outcome memory deposit failed: %s", exc)
        return None


def _persist_update(cortex: "Cortex", experiment: Experiment) -> None:
    """Write the experiment's UPDATED state back to the experiment_queue."""
    try:
        with cortex._db() as conn:
            conn.execute(
                "UPDATE experiment_queue SET status = %s, "
                "experiment_json = %s, completed_at = %s "
                "WHERE experiment_id = %s",
                (
                    experiment.status.value,
                    experiment.to_json(),
                    _now_iso(),
                    experiment.experiment_id,
                ),
            )
    except Exception as exc:
        logger.warning("experiment_outcome persist failed: %s", exc)


def _push_committed(cortex: "Cortex", experiment: Experiment) -> None:
    """Push the UPDATED outcome to TWM with cp1_provisional=False — this
    one is committed, not provisional."""
    try:
        cortex.twm_push(
            source="experiment_outcome",
            content_csb=(
                f"EXPERIMENT_UPDATED {experiment.experiment_id} "
                f"reason={experiment.update.reason}"
            ),
            salience=0.65,
            metadata={
                "type": "experiment_updated",
                "experiment_id": experiment.experiment_id,
                "outcome": experiment.observation.outcome.value,
                "cp1_provisional": False,
            },
            category="experiment_updated",
        )
    except Exception as exc:
        logger.warning("experiment_outcome twm_push failed: %s", exc)


def apply_outcome(cortex: "Cortex", experiment: Experiment) -> Experiment:
    """Take an OBSERVED experiment, derive its Update, apply effects, and
    transition to UPDATED. Returns the (now terminal) experiment.
    """
    if experiment.status != ExperimentStatus.OBSERVED:
        raise ValueError(
            f"apply_outcome requires status=OBSERVED, got {experiment.status.value}"
        )

    update = derive_update(experiment)

    # Apply trail edges
    for change in update.trail_edge_changes:
        _apply_trail_edge(cortex, change)

    # Deposit the memory and replace the placeholder accretion id
    real_id = _deposit_memory(cortex, experiment, update)
    if real_id:
        update.memory_accretions = [
            real_id if a.startswith("PENDING:") else a for a in update.memory_accretions
        ]

    # Inhibitor deltas: persist as a tagged memory the inhibitor reader
    # can pick up. (The full inhibitor-pattern primitive is T-#419; until
    # then we leave a structured note on the experiment.)
    if update.inhibitor_weight_deltas:
        try:
            from ..memory.models import Memory, MemoryType

            mem = Memory(
                narrative=(
                    f"inhibitor delta from experiment "
                    f"{experiment.experiment_id}: "
                    f"{json.dumps(update.inhibitor_weight_deltas)}"
                ),
                memory_type=MemoryType.PROCEDURAL,
                metadata={
                    "type": "inhibitor_delta",
                    "experiment_id": experiment.experiment_id,
                    "deltas": update.inhibitor_weight_deltas,
                },
                source="experiment",
            )
            cortex.store(mem)
        except Exception as exc:
            logger.warning("experiment_outcome inhibitor delta deposit failed: %s", exc)

    experiment.apply_update(update)
    _persist_update(cortex, experiment)
    _push_committed(cortex, experiment)
    return experiment


def next_observed(cortex: "Cortex") -> Optional[Experiment]:
    """Pull the oldest OBSERVED experiment from the queue, or None."""
    try:
        with cortex._db() as conn:
            conn.execute(
                "SELECT experiment_json FROM experiment_queue "
                "WHERE status = %s ORDER BY enqueued_at LIMIT 1",
                (ExperimentStatus.OBSERVED.value,),
            )
            row = conn.fetchone()
    except Exception as exc:
        logger.warning("experiment_outcome next_observed failed: %s", exc)
        return None
    if not row:
        return None
    return Experiment.from_json(row[0])


def feedback_tick(cortex: "Cortex") -> Optional[Experiment]:
    """Pick the next OBSERVED experiment and apply its outcome. Returns the
    UPDATED experiment, or None if the queue has nothing OBSERVED."""
    experiment = next_observed(cortex)
    if experiment is None:
        return None
    return apply_outcome(cortex, experiment)
