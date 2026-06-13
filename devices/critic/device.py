"""Critic device — validates builder decisions and extracts improvement patterns."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from unseen_university.device import BaseDevice, INTERFACE_VERSION

from .agent import CriticAgent, Decision

log = logging.getLogger(__name__)


class CriticDevice(BaseDevice):
    """Evaluates builder decisions to enable learning."""

    def __init__(self) -> None:
        """Initialize Critic device."""
        super().__init__("critic")
        self._agent = CriticAgent()
        self._judgments: dict = {}

    def who_am_i(self) -> dict:
        """Device identity."""
        return {
            "name": "Critic",
            "role": "master",
            "purpose": "validates builder decisions and extracts improvement patterns",
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
        log.warning("Critic: blocked — %s", reason)

    def halt(self) -> None:
        pass

    def recovery(self) -> None:
        pass

    def evaluate_replay(self, ticket_id: str, replay_data: dict) -> dict:
        """Evaluate a ticket replay using critic analysis.

        Args:
            ticket_id: ID of ticket being analyzed
            replay_data: Output from replay_and_analyze (event_count, turns, decision_points)

        Returns:
            Analysis with verdicts, patterns, and improvement opportunities
        """
        log.info("Critic: evaluating replay for %s", ticket_id)

        verdicts = []
        for turn in replay_data.get("turns", []):
            decision = Decision(
                ticket_id=ticket_id,
                turn_num=turn["turn"],
                decision_point=turn["decision_point"],
                choice=turn["tool"],
                context={"ticket": ticket_id},
                tool_result=turn.get("tool_result"),  # Actual error/success message
            )
            judgment = self._agent.evaluate_decision(decision)
            verdicts.append(judgment)

        # Analyze patterns
        pattern_analysis = self._agent.analyze_pattern(verdicts)

        result = {
            "ticket_id": ticket_id,
            "verdict_count": len(verdicts),
            "verdict_distribution": pattern_analysis["verdict_distribution"],
            "common_patterns": pattern_analysis["common_patterns"],
            "failure_modes": pattern_analysis["failure_modes"],
            "improvement_opportunities": pattern_analysis["improvement_opportunities"],
        }

        self._judgments[ticket_id] = result
        log.info("Critic: evaluation complete for %s — %s", ticket_id, result)
        return result

    def get_judgments(self, ticket_id: str | None = None) -> dict:
        """Get stored judgments."""
        if ticket_id:
            return self._judgments.get(ticket_id, {})
        return self._judgments
