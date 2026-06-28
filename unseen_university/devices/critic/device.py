"""Critic device — evaluates builder decisions, extracts patterns, learns improvement rules."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from unseen_university.device import BaseDevice, INTERFACE_VERSION

from .agent import CriticAgent, CriticJudgment, Decision
from unseen_university.devices.evaluator.core import EvaluatorCore

log = logging.getLogger(__name__)

_RULES_DIR = Path.home() / ".unseen_university" / "critic_rules"
_CRITIC_MODEL = "anthropic/claude-haiku-4-5-20251001"

_DECISION_CRITERIA = [
    {
        "name": "correctness",
        "instruction": "Was the tool choice correct given the decision context?",
    },
    {
        "name": "efficiency",
        "instruction": "Did the tool advance the task without unnecessary side-effects?",
    },
    {
        "name": "error_handling",
        "instruction": "Were errors detected and recovered from appropriately?",
    },
]


class CriticDevice(BaseDevice):
    """Evaluates builder decisions and learns improvement rules from patterns."""

    def __init__(self, inference_device=None) -> None:
        super().__init__("critic")
        self._agent = CriticAgent()
        self._inference = inference_device
        self._judgments: dict = {}
        self._load_rules()

    def who_am_i(self) -> dict:
        return {
            "name": "Critic",
            "role": "master",
            "purpose": "evaluates builder decisions, extracts failure patterns, and learns improvement rules",
            "version": "0.2",
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
        return {"current_version": "0.2.0"}

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

    # ── Inference helper ──────────────────────────────────────────────────────

    def _get_inference(self):
        if self._inference is None:
            from unseen_university.devices.inference.device import InferenceDevice
            self._inference = InferenceDevice()
        return self._inference

    # ── EvaluatorCore-backed evaluation ───────────────────────────────────────

    def evaluate_decision(self, decision: Decision) -> CriticJudgment:
        """Evaluate a builder decision via EvaluatorCore(optimism=-1.0).

        Uses a fault-finding stance — errs toward identifying problems.
        Output shape matches CriticJudgment for DickSimnel compat.
        Falls back to CriticAgent heuristic evaluation if inference fails.
        """
        context = (
            f"Ticket: {decision.ticket_id}\n"
            f"Turn: {decision.turn_num}\n"
            f"Decision point: {decision.decision_point}\n"
            f"Tool chosen: {decision.choice}\n"
            f"Tool result: {decision.tool_result or '(no result)'}"
        )
        try:
            core = EvaluatorCore(self._get_inference(), model=_CRITIC_MODEL)
            result = core.evaluate(context, _DECISION_CRITERIA, optimism=-1.0)
            passed = result.get("passed", False)
            score = result.get("score", 0.0)
            verdict = "good" if passed and score >= 0.6 else ("bad" if score < 0.3 else "neutral")
            failed_criteria = [
                c["name"] for c in result.get("criteria_results", []) if not c.get("passed", True)
            ]
            pattern = failed_criteria[0] if failed_criteria else ("successful_forward_progress" if passed else None)
            reasoning = "; ".join(
                f"{c['name']}: {c.get('reasoning', '')[:80]}"
                for c in result.get("criteria_results", [])
            ) or result.get("raw_response", "")[:200]
            improvement = None if passed else f"Address failure in: {', '.join(failed_criteria)}"
            log.info(
                "Critic.evaluate_decision (EvaluatorCore)|ticket=%s|turn=%d|verdict=%s|score=%.2f",
                decision.ticket_id, decision.turn_num, verdict, score,
            )
            return CriticJudgment(
                decision=decision,
                verdict=verdict,
                confidence=score,
                reasoning=reasoning,
                pattern=pattern,
                improvement=improvement,
            )
        except Exception as exc:
            log.warning("Critic.evaluate_decision: EvaluatorCore failed, using heuristic: %s", exc)
            return self._agent.evaluate_decision(decision)

    # ── Replay evaluation ─────────────────────────────────────────────────────

    def evaluate_replay(self, ticket_id: str, replay_data: dict) -> dict:
        """Evaluate a ticket replay using EvaluatorCore(optimism=-1.0) per turn."""
        log.info("Critic: evaluating replay for %s", ticket_id)

        verdicts = []
        for turn in replay_data.get("turns", []):
            decision = Decision(
                ticket_id=ticket_id,
                turn_num=turn["turn"],
                decision_point=turn["decision_point"],
                choice=turn["tool"],
                context={"ticket": ticket_id},
                tool_result=turn.get("tool_result"),
            )
            verdicts.append(self.evaluate_decision(decision))

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
        log.info("Critic: evaluation complete for %s", ticket_id)
        return result

    def get_judgments(self, ticket_id: str | None = None) -> dict:
        if ticket_id:
            return self._judgments.get(ticket_id, {})
        return self._judgments

    # ── Learning (formerly Improver) ─────────────────────────────────────────

    def learn_from_critic(self, critic_analysis: dict) -> dict:
        """Learn rules from a pattern analysis. Persists rules to disk."""
        log.info("Critic: learning from analysis")
        rules = self._agent.learn_from_patterns(critic_analysis)
        self._save_rules()

        return {
            "rules_learned": len(rules),
            "rules": self._agent.export_rules(),
            "stats": self._agent.get_stats(),
        }

    def get_recommendation(self, decision_context: dict) -> dict | None:
        """Get a rule-based recommendation for the current decision context."""
        return self._agent.apply_rules(decision_context)

    def record_outcome(self, rule_name: str, success: bool) -> None:
        self._agent.record_improvement(rule_name, success)

    def get_stats(self) -> dict:
        return self._agent.get_stats()

    def _load_rules(self) -> None:
        rules_file = _RULES_DIR / "rules.json"
        if rules_file.exists():
            try:
                with open(rules_file) as f:
                    self._agent.load_rules(json.load(f))
            except Exception as e:
                log.warning("Critic: failed to load rules: %s", e)

    def _save_rules(self) -> None:
        _RULES_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(_RULES_DIR / "rules.json", "w") as f:
                json.dump(self._agent.export_rules(), f, indent=2)
            log.info("Critic: saved %d rules", len(self._agent.export_rules()))
        except Exception as e:
            log.error("Critic: failed to save rules: %s", e)
