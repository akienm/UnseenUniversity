"""
experiment_cascade.py — T-substrate-experiment-cascade

The conductor that walks cascade levels in order before escalating to
an LLM. Each level is an Experiment-shaped sub-probe against Igor's
existing substrate. The walker:

  1. Forms a level-appropriate hypothesis
  2. Runs the level's probe
  3. Compares expected vs actual via Experiment machinery
  4. On match: returns (level, result)
  5. On lever surfaced: aborts current level, restarts cascade with lever
  6. On exhaustion: advances to next level
  7. Only after all substrate levels exhaust does it escalate to LLM

Biomimetic claim (not phenomenological): the cascade is architecturally
explicit with discrete levels, but subjectively continuous. Akien's felt
experience is "well shit, I don't recall a solution, so I'll try an
experiment" — the levels are invisible from the outside. Biology endorses:
predictive coding runs this cascade at millisecond timescales
through V1→V2→V4→IT→PFC; cerebellum→cortex motor escalation is the same
shape; hippocampal completion→separation→escalate-to-cortex is the same
shape.

## Levels (concrete, plug into shipped machinery)

- Level 0 — exact recall (cortex.search / get / direct lookup)
- Level 1 — widen-on-miss (shipped today, commit 7dcab891)
- Level 2 — interpretive_edge traversal (spread activation, STUB for MVP)
- Level 3 — tool combination (cheap tool chain, STUB for MVP)
- Level 4 — past-experiment lookup in experiment_queue (STUB for MVP)
- Level 5 — LLM reasoning workflows (STUB, downstream)

Levels 2-4 register as stubs that return EXHAUSTED; concrete
implementations are separate tickets. Level 5 returns ESCALATE without
actually calling the LLM — that's T-reasoning-workflow-primitive.

## CP grounding

- CP1 — each level honestly reports 'I don't know' when empty
- CP2 — mismatches are learning signal, recorded via Experiment.update
- CP3 — each level explains its escalation reason
- CP6 — LLM escalation is last resort; every cheaper verification runs
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, Protocol

from .experiment import (
    Experiment,
    ExperimentStatus,
    Hypothesis,
    Observation,
    Outcome,
    Probe,
    ProbeKind,
    Update,
)

if TYPE_CHECKING:
    from ..memory.cortex import Cortex

logger = logging.getLogger(__name__)


# ── Result shape ─────────────────────────────────────────────────────────────


class CascadeStatus(str, Enum):
    """Outcome of a single cascade level's probe."""

    MATCHED = "matched"  # probe found the answer at this level
    EXHAUSTED = "exhausted"  # probe ran, came up empty, no levers surfaced
    LEVERAGED = "leveraged"  # probe found something unexpected → restart cascade
    ESCALATE = "escalate"  # no more substrate levels; hand to LLM


@dataclass
class CascadeResult:
    """The output of a single cascade level, or of a full walker run."""

    status: CascadeStatus
    level_name: str
    data: Any = None
    """The matched result (MATCHED), the leverage info (LEVERAGED), or
    None (EXHAUSTED / ESCALATE)."""

    reason: str = ""
    """Human-readable explanation. CP3: the 'why' of this outcome."""

    experiment: Optional[Experiment] = None
    """The Experiment that ran at this level. Carries hypothesis, probe,
    observation, and update for audit."""

    def is_terminal(self) -> bool:
        """Does this result end the walker, or should we keep going?"""
        return self.status in (CascadeStatus.MATCHED, CascadeStatus.ESCALATE)


# ── Situation shape ──────────────────────────────────────────────────────────


@dataclass
class CascadeSituation:
    """Input to the cascade walker — what Igor is trying to resolve.

    Minimal MVP shape. Extend as the cascade learns what it needs.
    """

    query: str
    """Natural-language or keyword description of what we're looking for."""

    context: dict[str, Any] = field(default_factory=dict)
    """Current attractors, milieu summary, recent TWM observations — anything
    that scopes the search. Optional."""

    target_shape: str = "any"
    """What kind of answer we want: 'memory', 'facia', 'tool_output', 'any'.
    Lets levels decide if their probe shape is a good fit."""


# ── Level protocol ───────────────────────────────────────────────────────────


class CascadeLevel(Protocol):
    """Abstract contract every cascade level implements.

    A level is a wrapped Experiment-shaped probe. It forms a hypothesis
    appropriate to its level of abstraction, runs a probe against the
    substrate, observes the result, and returns a CascadeResult that the
    walker uses to decide whether to stop or continue.
    """

    name: str

    def try_probe(
        self, cortex: "Cortex", situation: CascadeSituation
    ) -> CascadeResult: ...


# ── Concrete levels ─────────────────────────────────────────────────────────


class Level0ExactRecall:
    """Level 0: cheapest — direct retrieval from cortex.

    Hypothesis: the answer is directly in memory under the query as stated.
    Probe: cortex.search(query) with exact terms, small limit.
    Match: any result returned.
    Exhaustion: empty result set with no signal for wider search.
    """

    name = "level_0_exact_recall"

    def try_probe(self, cortex: "Cortex", situation: CascadeSituation) -> CascadeResult:
        hypothesis = Hypothesis(
            statement=f"the answer to {situation.query!r} is in direct memory",
            source="cascade_level_0",
            confidence=0.3,
        )
        probe = Probe(
            kind=ProbeKind.MEMORY_QUERY,
            target=situation.query,
            payload={"query": situation.query, "limit": 5},
            expected_shape="at least one matching memory",
        )
        experiment = Experiment(hypothesis=hypothesis, probe=probe)
        experiment.advance(ExperimentStatus.RUNNING)

        try:
            results = cortex.search(situation.query, limit=5)
        except Exception as exc:
            logger.debug("level_0 cortex.search failed: %s", exc)
            obs = Observation(
                outcome=Outcome.INCONCLUSIVE,
                data={"error": type(exc).__name__, "detail": str(exc)[:200]},
                notes="cortex.search raised",
            )
            experiment.record_observation(obs)
            return CascadeResult(
                status=CascadeStatus.EXHAUSTED,
                level_name=self.name,
                reason=f"cortex.search raised {type(exc).__name__}",
                experiment=experiment,
            )

        n = len(results) if results else 0
        if n > 0:
            obs = Observation(
                outcome=Outcome.MATCH,
                data={"result_count": n},
                notes=f"level 0 returned {n} direct matches",
            )
            experiment.record_observation(obs)
            return CascadeResult(
                status=CascadeStatus.MATCHED,
                level_name=self.name,
                data=results,
                reason=f"level 0 exact recall hit ({n} results)",
                experiment=experiment,
            )

        obs = Observation(
            outcome=Outcome.INCONCLUSIVE,
            data={"result_count": 0},
            notes="level 0 empty, no lever surfaced",
        )
        experiment.record_observation(obs)
        return CascadeResult(
            status=CascadeStatus.EXHAUSTED,
            level_name=self.name,
            reason="level 0 exact recall empty",
            experiment=experiment,
        )


class Level1WidenOnMiss:
    """Level 1: widen-on-miss fallback.

    Hypothesis: the answer is in memory but under different phrasing.
    Probe: search_widen (shipped today) — token-LIKE, word-graph
    neighbor expansion, pg_trgm similarity.
    Match: any widen strategy returns results.
    Exhaustion: all widen strategies empty.
    """

    name = "level_1_widen_on_miss"

    def try_probe(self, cortex: "Cortex", situation: CascadeSituation) -> CascadeResult:
        hypothesis = Hypothesis(
            statement=(
                f"the answer to {situation.query!r} is in memory under "
                "different phrasing — try loosened retrieval"
            ),
            source="cascade_level_1",
            confidence=0.4,
        )
        probe = Probe(
            kind=ProbeKind.MEMORY_QUERY,
            target=situation.query,
            payload={"widen": True},
            expected_shape="structural anchor (facia, named node) via widen",
        )
        experiment = Experiment(hypothesis=hypothesis, probe=probe)
        experiment.advance(ExperimentStatus.RUNNING)

        try:
            from ..memory.search_widen import widen_search

            results, strategy = widen_search(
                cortex,
                situation.query,
                push_to_twm=False,  # walker logs its own TWM marker
            )
        except Exception as exc:
            logger.debug("level_1 widen_search failed: %s", exc)
            obs = Observation(
                outcome=Outcome.INCONCLUSIVE,
                data={"error": type(exc).__name__},
                notes="widen_search raised",
            )
            experiment.record_observation(obs)
            return CascadeResult(
                status=CascadeStatus.EXHAUSTED,
                level_name=self.name,
                reason=f"widen_search raised {type(exc).__name__}",
                experiment=experiment,
            )

        n = len(results) if results else 0
        if n > 0:
            obs = Observation(
                outcome=Outcome.MATCH,
                data={"result_count": n, "strategy": strategy},
                notes=f"level 1 widen ({strategy}) returned {n}",
            )
            experiment.record_observation(obs)
            return CascadeResult(
                status=CascadeStatus.MATCHED,
                level_name=self.name,
                data=results,
                reason=f"level 1 widen-on-miss hit via {strategy}",
                experiment=experiment,
            )

        obs = Observation(
            outcome=Outcome.INCONCLUSIVE,
            data={"result_count": 0},
            notes="level 1 widen exhausted",
        )
        experiment.record_observation(obs)
        return CascadeResult(
            status=CascadeStatus.EXHAUSTED,
            level_name=self.name,
            reason="level 1 widen strategies all empty",
            experiment=experiment,
        )


class _StubLevel:
    """Placeholder for levels whose concrete implementation is a separate
    sub-ticket. Always returns EXHAUSTED so the walker advances."""

    def __init__(self, name: str, reason: str) -> None:
        self.name = name
        self._reason = reason

    def try_probe(self, cortex: "Cortex", situation: CascadeSituation) -> CascadeResult:
        return CascadeResult(
            status=CascadeStatus.EXHAUSTED,
            level_name=self.name,
            reason=self._reason,
        )


class Level5LLMEscalationStub:
    """Level 5: would escalate to LLM reasoning workflows.

    MVP stub — returns ESCALATE with metadata describing what the
    walker would hand off to the LLM. Actual LLM wiring lives in
    T-reasoning-workflow-primitive + T-llm-collaboration-protocol.
    """

    name = "level_5_llm_reasoning"

    def try_probe(self, cortex: "Cortex", situation: CascadeSituation) -> CascadeResult:
        handoff = {
            "query": situation.query,
            "context": situation.context,
            "target_shape": situation.target_shape,
            "handoff_ts": datetime.now(timezone.utc).isoformat(),
        }
        return CascadeResult(
            status=CascadeStatus.ESCALATE,
            level_name=self.name,
            data=handoff,
            reason=(
                "substrate levels exhausted; would hand off to "
                "LLM reasoning workflow (stub)"
            ),
        )


# ── The walker ───────────────────────────────────────────────────────────────


DEFAULT_LEVEL_BUDGET: int = 10
"""Max total level-probe calls per cascade run. Prevents infinite loops
from lever-interrupt flipping between two levels. Conservative default;
tune as cascades get wired."""


class ExperimentCascade:
    """The walker. Iterates registered levels in order until one matches,
    one surfaces a lever (restart), or all exhaust (escalate).

    Single-call API from outside: `cascade.attempt(situation)`. Inside it
    walks discretely; from outside it feels continuous.
    """

    def __init__(
        self,
        cortex: "Cortex",
        level_budget: int = DEFAULT_LEVEL_BUDGET,
    ) -> None:
        self.cortex = cortex
        self.level_budget = level_budget
        self._levels: list[CascadeLevel] = []

    def register(self, level: CascadeLevel) -> None:
        """Add a level to the walk order. Order of register calls is the
        walker order."""
        self._levels.append(level)

    def attempt(self, situation: CascadeSituation) -> CascadeResult:
        """Walk the cascade. Returns the first MATCHED / ESCALATE result,
        or EXHAUSTED if budget runs out before any level lands.
        """
        if not self._levels:
            return CascadeResult(
                status=CascadeStatus.EXHAUSTED,
                level_name="no_levels_registered",
                reason="cascade has no levels registered",
            )

        budget_remaining = self.level_budget
        current_situation = situation
        while budget_remaining > 0:
            for level in self._levels:
                budget_remaining -= 1
                if budget_remaining < 0:
                    break

                try:
                    result = level.try_probe(self.cortex, current_situation)
                except Exception as exc:
                    logger.warning(
                        "cascade level %s raised: %s — treating as EXHAUSTED",
                        level.name,
                        exc,
                    )
                    result = CascadeResult(
                        status=CascadeStatus.EXHAUSTED,
                        level_name=level.name,
                        reason=f"level raised {type(exc).__name__}",
                    )

                if result.status == CascadeStatus.MATCHED:
                    self._push_outcome_marker(situation, result)
                    return result
                if result.status == CascadeStatus.ESCALATE:
                    self._push_outcome_marker(situation, result)
                    return result
                if result.status == CascadeStatus.LEVERAGED:
                    # Restart the cascade with the leveraged situation.
                    # T-lever-interrupt-pattern will formalize this path;
                    # for now, MVP just re-enters the outer while with the
                    # new situation.
                    if isinstance(result.data, CascadeSituation):
                        current_situation = result.data
                    break  # exit inner for, re-enter while
                # EXHAUSTED → continue to next level in inner for loop
            else:
                # Inner for completed all levels with EXHAUSTED
                final = CascadeResult(
                    status=CascadeStatus.EXHAUSTED,
                    level_name="all_levels_exhausted",
                    reason="every registered level returned exhausted without levers",
                )
                self._push_outcome_marker(situation, final)
                return final
        # Budget exhausted
        final = CascadeResult(
            status=CascadeStatus.EXHAUSTED,
            level_name="budget_exhausted",
            reason=f"cascade budget ({self.level_budget}) exhausted",
        )
        self._push_outcome_marker(situation, final)
        return final

    def _push_outcome_marker(
        self, situation: CascadeSituation, result: CascadeResult
    ) -> None:
        """Emit a TWM marker describing the cascade walk outcome. Lets
        cognition see where the cascade ended up without reading internal
        state."""
        try:
            self.cortex.twm_push(
                source="experiment_cascade",
                content_csb=(
                    f"CASCADE_{result.status.value.upper()} "
                    f"query={situation.query!r} "
                    f"level={result.level_name} "
                    f"reason={result.reason}"
                ),
                salience=0.5,
                category="cascade_walk",
                metadata={
                    "type": "cascade_walk",
                    "status": result.status.value,
                    "level_name": result.level_name,
                    "query": situation.query,
                    "target_shape": situation.target_shape,
                    "reason": result.reason,
                    "cp1_provisional": True,
                },
            )
        except Exception as exc:
            logger.debug("cascade _push_outcome_marker failed: %s", exc)


# ── Default cascade assembly ─────────────────────────────────────────────────


def build_default_cascade(cortex: "Cortex") -> ExperimentCascade:
    """Construct the default cascade with levels 0-5 wired in order.

    Levels 2-4 are stubs for now (separate sub-tickets will wire them
    to interpretive_edges traversal, tool combination, and
    experiment_queue lookup respectively). Level 5 is an escalation
    stub — no LLM call — pending T-reasoning-workflow-primitive.
    """
    cascade = ExperimentCascade(cortex)
    cascade.register(Level0ExactRecall())
    cascade.register(Level1WidenOnMiss())
    cascade.register(
        _StubLevel(
            name="level_2_interpretive_traversal",
            reason="level 2 stub — interpretive_edge BFS traversal not yet wired",
        )
    )
    cascade.register(
        _StubLevel(
            name="level_3_tool_combination",
            reason="level 3 stub — cheap tool chain not yet wired",
        )
    )
    cascade.register(
        _StubLevel(
            name="level_4_past_experiment_lookup",
            reason="level 4 stub — experiment_queue hypothesis-shape lookup not yet wired",
        )
    )
    cascade.register(Level5LLMEscalationStub())
    return cascade
