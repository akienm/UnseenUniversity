"""Improver device — applies learned patterns to improve builder decisions."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from unseen_university.device import BaseDevice, INTERFACE_VERSION

from .agent import ImproverAgent

log = logging.getLogger(__name__)

_RULES_DIR = Path.home() / ".unseen_university" / "improver_rules"


class ImproverDevice(BaseDevice):
    """Applies Critic's patterns to improve future decisions."""

    def __init__(self) -> None:
        """Initialize Improver device."""
        super().__init__("improver")
        self._agent = ImproverAgent()
        self._load_rules()

    def who_am_i(self) -> dict:
        """Device identity."""
        return {
            "name": "Improver",
            "role": "master",
            "purpose": "applies learned patterns to improve builder decisions",
            "version": "0.1",
        }

    def health(self) -> dict:
        return {"status": "healthy", "detail": "analyst device", "checked_at": ""}

    def startup_errors(self) -> list:
        return []

    def requirements(self) -> dict:
        return {"deps": [], "env": []}

    def capabilities(self) -> dict:
        return {"can_send": False, "can_receive": False}

    def comms(self) -> dict:
        return {"address": "none", "mode": "none"}

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def uptime(self) -> float:
        return 0.0

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0"}

    def where_and_how(self) -> dict:
        return {"host": "localhost", "pid": 0}

    def restart(self) -> None:
        pass

    def block(self, reason: str) -> None:
        log.warning("Improver: blocked — %s", reason)

    def halt(self) -> None:
        pass

    def recovery(self) -> None:
        pass

    def learn_from_critic(self, critic_analysis: dict) -> dict:
        """Learn from Critic's pattern analysis.

        Args:
            critic_analysis: Output from Critic.analyze_pattern()

        Returns:
            Result dict with rules learned and stats
        """
        log.info("Improver: learning from critic analysis")
        rules = self._agent.learn_from_patterns(critic_analysis)

        result = {
            "rules_learned": len(rules),
            "rules": self._agent.export_rules(),
            "stats": self._agent.get_stats(),
        }

        # Persist rules
        self._save_rules()

        log.info("Improver: learned %d rules, saved to disk", len(rules))
        return result

    def get_recommendation(self, decision_context: dict) -> dict | None:
        """Get a recommendation for the current decision.

        Args:
            decision_context: Dict with ticket, turn, decision_point, outcome

        Returns:
            Recommendation dict or None if no rules apply
        """
        return self._agent.apply_rules(decision_context)

    def record_outcome(self, rule_name: str, success: bool) -> None:
        """Record whether a recommendation led to improvement."""
        self._agent.record_improvement(rule_name, success)

    def get_stats(self) -> dict:
        """Get improvement statistics."""
        return self._agent.get_stats()

    def _load_rules(self) -> None:
        """Load persisted rules from disk."""
        rules_file = _RULES_DIR / "rules.json"
        if rules_file.exists():
            try:
                with open(rules_file) as f:
                    rules_data = json.load(f)
                    for r in rules_data:
                        # Recreate LearningRule objects
                        from .agent import LearningRule
                        rule = LearningRule(
                            pattern_name=r["pattern"],
                            condition=r["condition"],
                            action=r["action"],
                            confidence=r["confidence"],
                        )
                        self._agent._rules.append(rule)
                log.info("Improver: loaded %d rules from disk", len(rules_data))
            except Exception as e:
                log.warning("Improver: failed to load rules: %s", e)

    def _save_rules(self) -> None:
        """Persist rules to disk."""
        _RULES_DIR.mkdir(parents=True, exist_ok=True)
        rules_file = _RULES_DIR / "rules.json"
        try:
            with open(rules_file, "w") as f:
                json.dump(self._agent.export_rules(), f, indent=2)
            log.info("Improver: saved %d rules to disk", len(self._agent._rules))
        except Exception as e:
            log.error("Improver: failed to save rules: %s", e)
