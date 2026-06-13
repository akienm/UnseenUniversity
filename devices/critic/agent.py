"""
Critic agent — validates builder decisions and extracts improvement patterns.

The Critic watches DickSimnel (or any builder) make decisions and evaluates:
1. Was this decision correct?
2. What context led to this decision?
3. What patterns emerge from good vs bad decisions?

Foundation for builder learning loop: observe → evaluate → extract patterns → improve.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class Decision:
    """A single decision point from a builder's execution."""
    ticket_id: str
    turn_num: int
    decision_point: str  # "tool_selection", "param_choice", etc
    choice: str  # The tool/param the builder chose
    context: dict  # Full context: ticket, prior turns, system prompt
    tool_result: str | None  # What happened when we executed the choice


@dataclass
class CriticJudgment:
    """Critic's evaluation of a decision."""
    decision: Decision
    verdict: str  # "good", "bad", "neutral", "unclear"
    confidence: float  # 0.0 to 1.0
    reasoning: str  # Why the Critic thinks this verdict is correct
    pattern: str | None  # Recurring pattern this decision represents
    improvement: str | None  # How the builder could decide better next time


class CriticAgent:
    """Evaluates builder decisions to enable learning and improvement."""

    def __init__(self) -> None:
        """Initialize Critic agent."""
        self._judgments: list[CriticJudgment] = []

    def evaluate_decision(self, decision: Decision) -> CriticJudgment:
        """Evaluate whether a builder's decision was correct.

        Args:
            decision: A builder's choice at a decision point

        Returns:
            CriticJudgment: Evaluation with verdict, confidence, reasoning
        """
        log.info(
            "Critic: evaluating decision for %s turn %d — %s → %s",
            decision.ticket_id, decision.turn_num, decision.decision_point, decision.choice,
        )

        # Gather context for judgment
        ticket = decision.context.get("ticket", {})
        prior_turns = decision.context.get("prior_turns", [])
        tool_result = decision.tool_result

        # JUDGMENT LOGIC
        # Bad decision: tool call failed, builder tried it anyway
        if tool_result and ("error" in tool_result.lower() or "failed" in tool_result.lower()):
            judgment = CriticJudgment(
                decision=decision,
                verdict="bad",
                confidence=0.9,
                reasoning=f"Tool {decision.choice} was called but failed: {tool_result[:100]}",
                pattern="error_not_recovered",
                improvement=f"Detect failures early and try a different approach instead of retrying {decision.choice}",
            )
        # Good decision: tool succeeded and moved work forward
        elif tool_result and "success" in tool_result.lower():
            judgment = CriticJudgment(
                decision=decision,
                verdict="good",
                confidence=0.85,
                reasoning=f"Tool {decision.choice} succeeded and advanced the ticket",
                pattern="successful_forward_progress",
                improvement=None,
            )
        # Neutral: executed but no clear success/failure signal
        elif tool_result:
            judgment = CriticJudgment(
                decision=decision,
                verdict="neutral",
                confidence=0.5,
                reasoning=f"Tool {decision.choice} executed with result: {tool_result[:80]}",
                pattern=None,
                improvement="Consider clearer success/failure signals",
            )
        else:
            judgment = CriticJudgment(
                decision=decision,
                verdict="unclear",
                confidence=0.3,
                reasoning="No result recorded for this decision",
                pattern=None,
                improvement="Ensure all tool calls record their outcomes",
            )

        self._judgments.append(judgment)
        log.info(
            "Critic: judgment=%s confidence=%.2f for %s",
            judgment.verdict, judgment.confidence, decision.ticket_id,
        )
        return judgment

    def analyze_pattern(self, verdicts: list[CriticJudgment]) -> dict:
        """Extract patterns from a set of decisions.

        Args:
            verdicts: List of CriticJudgments from a replay session

        Returns:
            Pattern analysis dict with:
              - verdict_distribution: counts of good/bad/neutral decisions
              - common_patterns: recurring pattern names
              - failure_modes: what goes wrong consistently
              - improvement_opportunities: actionable improvements
        """
        from collections import Counter

        verdict_counts = Counter(v.verdict for v in verdicts)
        pattern_names = Counter(v.pattern for v in verdicts if v.pattern)
        failure_verdicts = [v for v in verdicts if v.verdict == "bad"]
        improvement_ideas = [v.improvement for v in verdicts if v.improvement]

        analysis = {
            "verdict_distribution": dict(verdict_counts),
            "common_patterns": dict(pattern_names.most_common(5)),
            "failure_count": len(failure_verdicts),
            "failure_modes": list(set(v.pattern for v in failure_verdicts if v.pattern)),
            "improvement_opportunities": list(set(improvement_ideas)),
        }

        log.info(
            "Critic: pattern analysis — %d good, %d bad, %d neutral decisions",
            verdict_counts.get("good", 0), verdict_counts.get("bad", 0), verdict_counts.get("neutral", 0),
        )
        return analysis

    def all_judgments(self) -> list[CriticJudgment]:
        """Return all judgments made by this Critic instance."""
        return self._judgments
