"""
Improver agent — applies learned patterns to improve future builder decisions.

The Improver takes Critic's pattern analysis and feeds it back:
1. Read patterns (failure modes, improvements)
2. Build decision rules from patterns
3. Apply rules to future decisions
4. Measure improvement (did it help?)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class LearningRule:
    """A decision rule learned from patterns."""
    pattern_name: str  # e.g., "error_not_recovered"
    condition: str  # What triggers this rule
    action: str  # What to do when triggered
    confidence: float  # How sure we are (0.0 to 1.0)


class ImproverAgent:
    """Applies learned patterns to improve builder decisions."""

    def __init__(self) -> None:
        """Initialize Improver."""
        self._rules: list[LearningRule] = []
        self._applied_count = 0
        self._improvement_count = 0

    def learn_from_patterns(self, critic_analysis: dict) -> list[LearningRule]:
        """Convert Critic's patterns into decision rules.

        Args:
            critic_analysis: Output from Critic.analyze_pattern()
              - verdict_distribution: good/bad/neutral counts
              - failure_modes: list of recurring failure patterns
              - improvement_opportunities: list of suggested improvements

        Returns:
            List of LearningRules derived from patterns
        """
        log.info("Improver: learning from patterns")
        rules = []

        # Rule 1: When we see "error_not_recovered", try alternatives first
        if "error_not_recovered" in (critic_analysis.get("failure_modes") or []):
            rule = LearningRule(
                pattern_name="error_not_recovered",
                condition="tool_call returns error",
                action="try alternative tool before retrying same tool",
                confidence=0.85,
            )
            rules.append(rule)
            log.info("Improver: created rule for error_not_recovered")

        # Rule 2: When we see "successful_forward_progress", prefer that tool
        if "successful_forward_progress" in (critic_analysis.get("common_patterns") or {}).keys():
            rule = LearningRule(
                pattern_name="successful_forward_progress",
                condition="tool_call succeeds",
                action="mark tool as preferred for this decision point",
                confidence=0.90,
            )
            rules.append(rule)
            log.info("Improver: created rule for successful_forward_progress")

        # Rule 3: Generic improvement from Critic suggestions
        improvements = critic_analysis.get("improvement_opportunities") or []
        for improvement in improvements[:2]:  # Top 2 improvements
            if improvement:
                rule = LearningRule(
                    pattern_name="improvement_suggestion",
                    condition="decision point reached",
                    action=improvement,
                    confidence=0.70,
                )
                rules.append(rule)
                log.info("Improver: created rule from suggestion: %s", improvement[:50])

        self._rules.extend(rules)
        log.info("Improver: learned %d rules", len(rules))
        return rules

    def apply_rules(self, decision_context: dict) -> dict | None:
        """Apply learned rules to a decision context.

        Args:
            decision_context: Dict with ticket, turn, decision_point, available_tools

        Returns:
            Recommendation dict: {action, confidence, rule_name}
            or None if no rules apply
        """
        for rule in self._rules:
            # Check if rule's condition matches context
            if self._rule_applies(rule, decision_context):
                self._applied_count += 1
                recommendation = {
                    "action": rule.action,
                    "confidence": rule.confidence,
                    "rule": rule.pattern_name,
                }
                log.info(
                    "Improver: applied rule %s with confidence %.2f",
                    rule.pattern_name, rule.confidence,
                )
                return recommendation

        return None

    def _rule_applies(self, rule: LearningRule, context: dict) -> bool:
        """Check if a rule's condition matches the current context."""
        # Simple matching: condition substring in decision_point or outcome
        decision_point = (context.get("decision_point") or "").lower()
        outcome = (context.get("outcome") or "").lower()
        condition_lower = rule.condition.lower()

        return condition_lower in decision_point or condition_lower in outcome

    def record_improvement(self, rule_name: str, success: bool) -> None:
        """Record whether applying a rule led to improvement."""
        if success:
            self._improvement_count += 1
        log.info(
            "Improver: recorded %s for rule %s (total: %d/%d)",
            "improvement" if success else "no change",
            rule_name,
            self._improvement_count,
            self._applied_count,
        )

    def get_stats(self) -> dict:
        """Get improvement statistics."""
        success_rate = (
            self._improvement_count / self._applied_count
            if self._applied_count > 0
            else 0.0
        )
        return {
            "rules_learned": len(self._rules),
            "rules_applied": self._applied_count,
            "improvements": self._improvement_count,
            "success_rate": success_rate,
        }

    def export_rules(self) -> list[dict]:
        """Export learned rules as JSON-serializable dicts."""
        return [
            {
                "pattern": r.pattern_name,
                "condition": r.condition,
                "action": r.action,
                "confidence": r.confidence,
            }
            for r in self._rules
        ]
