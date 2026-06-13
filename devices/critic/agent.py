"""
Critic agent — evaluates builder decisions, extracts patterns, and learns improvement rules.

Combines evaluation (was this decision correct?) with learning (what rules emerge?):
1. Evaluate individual decisions as good/bad/neutral
2. Analyze patterns across a set of decisions
3. Learn rules from patterns (formerly Improver)
4. Apply rules to future decision contexts
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class Decision:
    """A single decision point from a builder's execution."""
    ticket_id: str
    turn_num: int
    decision_point: str  # "tool_selection", "param_choice", etc.
    choice: str  # The tool/param the builder chose
    context: dict  # Full context: ticket, prior turns, system prompt
    tool_result: str | None  # What happened when we executed the choice


@dataclass
class CriticJudgment:
    """Critic's evaluation of a decision."""
    decision: Decision
    verdict: str  # "good", "bad", "neutral", "unclear"
    confidence: float  # 0.0 to 1.0
    reasoning: str
    pattern: str | None  # Recurring pattern this decision represents
    improvement: str | None  # How the builder could decide better next time


@dataclass
class LearningRule:
    """A decision rule learned from patterns."""
    pattern_name: str  # e.g., "error_not_recovered"
    condition: str  # What triggers this rule
    action: str  # What to do when triggered
    confidence: float  # 0.0 to 1.0


class CriticAgent:
    """Evaluates builder decisions and learns improvement rules from patterns."""

    def __init__(self) -> None:
        self._judgments: list[CriticJudgment] = []
        self._rules: list[LearningRule] = []
        self._applied_count = 0
        self._improvement_count = 0

    # ── Evaluation ────────────────────────────────────────────────────────────

    def evaluate_decision(self, decision: Decision) -> CriticJudgment:
        """Evaluate whether a builder's decision was correct."""
        log.info(
            "Critic: evaluating %s turn %d — %s → %s",
            decision.ticket_id, decision.turn_num, decision.decision_point, decision.choice,
        )

        tool_result = decision.tool_result or ""

        if "error" in tool_result.lower() or "failed" in tool_result.lower():
            judgment = CriticJudgment(
                decision=decision,
                verdict="bad",
                confidence=0.9,
                reasoning=f"Tool {decision.choice} failed: {tool_result[:100]}",
                pattern="error_not_recovered",
                improvement=f"Detect failures early and try a different approach instead of retrying {decision.choice}",
            )
        elif "success" in tool_result.lower():
            judgment = CriticJudgment(
                decision=decision,
                verdict="good",
                confidence=0.85,
                reasoning=f"Tool {decision.choice} succeeded and advanced the ticket",
                pattern="successful_forward_progress",
                improvement=None,
            )
        elif tool_result:
            judgment = CriticJudgment(
                decision=decision,
                verdict="neutral",
                confidence=0.5,
                reasoning=f"Tool {decision.choice} executed: {tool_result[:80]}",
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
        log.info("Critic: verdict=%s confidence=%.2f for %s", judgment.verdict, judgment.confidence, decision.ticket_id)
        return judgment

    def analyze_pattern(self, verdicts: list[CriticJudgment]) -> dict:
        """Extract patterns from a set of decisions."""
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
            "Critic: analysis — %d good, %d bad, %d neutral",
            verdict_counts.get("good", 0), verdict_counts.get("bad", 0), verdict_counts.get("neutral", 0),
        )
        return analysis

    def all_judgments(self) -> list[CriticJudgment]:
        return self._judgments

    # ── Learning (formerly Improver) ─────────────────────────────────────────

    def learn_from_patterns(self, critic_analysis: dict) -> list[LearningRule]:
        """Convert pattern analysis into decision rules."""
        log.info("Critic: learning from patterns")
        rules: list[LearningRule] = []

        if "error_not_recovered" in (critic_analysis.get("failure_modes") or []):
            rules.append(LearningRule(
                pattern_name="error_not_recovered",
                condition="tool_call returns error",
                action="try alternative tool before retrying same tool",
                confidence=0.85,
            ))

        if "successful_forward_progress" in (critic_analysis.get("common_patterns") or {}):
            rules.append(LearningRule(
                pattern_name="successful_forward_progress",
                condition="tool_call succeeds",
                action="mark tool as preferred for this decision point",
                confidence=0.90,
            ))

        for improvement in (critic_analysis.get("improvement_opportunities") or [])[:2]:
            if improvement:
                rules.append(LearningRule(
                    pattern_name="improvement_suggestion",
                    condition="decision point reached",
                    action=improvement,
                    confidence=0.70,
                ))

        self._rules.extend(rules)
        log.info("Critic: learned %d rules", len(rules))
        return rules

    def apply_rules(self, decision_context: dict) -> dict | None:
        """Apply learned rules to a decision context. Returns recommendation or None."""
        for rule in self._rules:
            if self._rule_applies(rule, decision_context):
                self._applied_count += 1
                log.info("Critic: applied rule %s (confidence %.2f)", rule.pattern_name, rule.confidence)
                return {
                    "action": rule.action,
                    "confidence": rule.confidence,
                    "rule": rule.pattern_name,
                }
        return None

    def _rule_applies(self, rule: LearningRule, context: dict) -> bool:
        decision_point = (context.get("decision_point") or "").lower()
        outcome = (context.get("outcome") or "").lower()
        cond = rule.condition.lower()
        return cond in decision_point or cond in outcome

    def record_improvement(self, rule_name: str, success: bool) -> None:
        if success:
            self._improvement_count += 1
        log.info("Critic: recorded %s for rule %s", "improvement" if success else "no change", rule_name)

    def get_stats(self) -> dict:
        rate = self._improvement_count / self._applied_count if self._applied_count > 0 else 0.0
        return {
            "rules_learned": len(self._rules),
            "rules_applied": self._applied_count,
            "improvements": self._improvement_count,
            "success_rate": rate,
        }

    def export_rules(self) -> list[dict]:
        return [
            {"pattern": r.pattern_name, "condition": r.condition, "action": r.action, "confidence": r.confidence}
            for r in self._rules
        ]

    def load_rules(self, rules_data: list[dict]) -> None:
        """Restore rules from persisted JSON."""
        for r in rules_data:
            self._rules.append(LearningRule(
                pattern_name=r["pattern"],
                condition=r["condition"],
                action=r["action"],
                confidence=r["confidence"],
            ))
        log.info("Critic: loaded %d rules", len(rules_data))
