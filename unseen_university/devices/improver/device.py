"""
ImproverDevice — EvaluatorCore(optimism=+1.0) wrapper for constructive improvement rules.

Takes CriticJudgment patterns and produces LearningRule objects by asking
the same EvaluatorCore infrastructure with a constructive (improvement-seeking)
stance. Where Critic finds what is wrong, Improver extracts what can be done better.

API:
  improve(patterns: list[CriticJudgment]) -> list[LearningRule]

Rules are persisted to disk (same shape as CriticAgent) so they survive restarts.

D-evaluator-consolidation-2026-06-14
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from unseen_university.device import BaseDevice, INTERFACE_VERSION
from unseen_university.devices.evaluator.core import EvaluatorCore
from unseen_university.devices.critic.agent import LearningRule

log = logging.getLogger(__name__)

_START_TIME = time.time()
_IMPROVER_MODEL = "anthropic/claude-haiku-4-5-20251001"
_RULES_DIR = Path.home() / ".unseen_university" / "improver_rules"

# Rubric for extracting improvement rules from a pattern summary.
_IMPROVEMENT_CRITERIA = [
    {
        "name": "identify_pattern",
        "instruction": (
            "Identify the underlying pattern or root cause that led to this behavior. "
            "Be specific and actionable."
        ),
    },
    {
        "name": "suggest_improvement",
        "instruction": (
            "Suggest a concrete improvement action that would prevent recurrence of this problem. "
            "Frame it positively — what should be done, not what was wrong."
        ),
    },
    {
        "name": "generalize_rule",
        "instruction": (
            "State a generalized decision rule that could apply to similar situations in future sprints. "
            "Format: 'When X, do Y.'"
        ),
    },
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ImproverDevice(BaseDevice):
    """Extracts constructive improvement rules from Critic pattern analysis."""

    DEVICE_ID = "improver"

    def __init__(self, inference_device=None) -> None:
        super().__init__()
        self._inference = inference_device
        self._rules: list[dict] = []
        self._errors: list[str] = []
        self._load_rules()

    # ── BaseDevice contract ───────────────────────────────────────────────────

    def who_am_i(self) -> dict:
        return {
            "device_id": self.DEVICE_ID,
            "name": "Improver",
            "version": "0.1.0",
            "purpose": "Constructive improvement rules from Critic pattern analysis",
        }

    def requirements(self) -> dict:
        return {"deps": [], "system": ["inference device reachable"]}

    def capabilities(self) -> dict:
        return {
            "can_send": False,
            "can_receive": True,
            "emitted_keywords": ["learning_rule"],
            "mcp_tools": ["improve"],
        }

    def comms(self) -> dict:
        return {
            "address": f"comms://{self.DEVICE_ID}/inbox",
            "mode": "read_write",
            "supports_push": False,
            "supports_pull": True,
            "supports_nudge": False,
        }

    def interface_version(self) -> str:
        return INTERFACE_VERSION

    def health(self) -> dict:
        if self._errors:
            return {"status": "degraded", "detail": self._errors[-1], "checked_at": _now()}
        return {"status": "healthy", "detail": "ready", "checked_at": _now()}

    def uptime(self) -> float:
        return time.time() - _START_TIME

    def startup_errors(self) -> list:
        return list(self._errors)

    def logs(self) -> dict:
        return {"paths": {}}

    def update_info(self) -> dict:
        return {"current_version": "0.1.0", "update_available": False}

    def where_and_how(self) -> dict:
        return {
            "host": os.uname().nodename,
            "pid": os.getpid(),
            "launch_command": "python -m unseen_university.devices.improver.device",
        }

    def restart(self) -> None:
        self._errors.clear()

    def block(self, reason: str) -> None:
        self._errors.append(f"blocked: {reason}")

    def halt(self) -> None:
        pass

    def recovery(self) -> None:
        self._errors.clear()

    # ── Inference helper ──────────────────────────────────────────────────────

    def _get_inference(self):
        if self._inference is None:
            from unseen_university.devices.inference.device import InferenceDevice
            self._inference = InferenceDevice()
        return self._inference

    # ── Core API ──────────────────────────────────────────────────────────────

    def improve(self, patterns) -> list[LearningRule]:
        """Extract constructive improvement rules from a list of CriticJudgment objects.

        Calls EvaluatorCore(optimism=+1.0) once per distinct failure pattern cluster.
        Returns a list of LearningRule objects that are also persisted to disk.
        """
        if not patterns:
            return []

        # Group by pattern name to cluster related judgments.
        clusters: dict[str, list] = {}
        for j in patterns:
            key = j.pattern or "general"
            clusters.setdefault(key, []).append(j)

        core = EvaluatorCore(self._get_inference(), model=_IMPROVER_MODEL)
        new_rules: list[LearningRule] = []

        for pattern_name, judgments in clusters.items():
            # Build a summary context for this cluster.
            verdicts = [j.verdict for j in judgments]
            reasonings = "; ".join(j.reasoning[:100] for j in judgments if j.reasoning)
            avg_confidence = sum(j.confidence for j in judgments) / len(judgments)
            context = (
                f"Pattern cluster: {pattern_name}\n"
                f"Occurrences: {len(judgments)}\n"
                f"Verdict distribution: {dict((v, verdicts.count(v)) for v in set(verdicts))}\n"
                f"Average confidence: {avg_confidence:.2f}\n"
                f"Examples: {reasonings[:400]}"
            )

            try:
                result = core.evaluate(context, _IMPROVEMENT_CRITERIA, optimism=1.0)
                score = result.get("score", 0.5)
                criteria_results = result.get("criteria_results", [])

                # Extract a single constructive rule from the criteria results.
                # Use the "suggest_improvement" criterion as the primary action.
                action_text = ""
                for crit in criteria_results:
                    if crit.get("name") == "suggest_improvement":
                        action_text = crit.get("reasoning", "")[:200]
                        break
                if not action_text:
                    # Fallback: use first passing criterion reasoning
                    for crit in criteria_results:
                        if crit.get("passed"):
                            action_text = crit.get("reasoning", "")[:200]
                            break

                if action_text:
                    confidence = round(min(1.0, avg_confidence * score), 4)
                    rule = LearningRule(
                        pattern_name=pattern_name,
                        condition=f"When pattern '{pattern_name}' is observed",
                        action=action_text,
                        confidence=confidence,
                    )
                    new_rules.append(rule)
                    log.info(
                        "ImproverDevice.improve: rule extracted pattern=%s confidence=%.2f",
                        pattern_name, confidence,
                    )
            except Exception as exc:
                log.warning("ImproverDevice.improve: EvaluatorCore failed for %s: %s", pattern_name, exc)
                self._errors.append(f"improve({pattern_name}): {exc}")

        # Persist rules and return.
        if new_rules:
            rule_dicts = [
                {
                    "pattern": r.pattern_name,
                    "condition": r.condition,
                    "action": r.action,
                    "confidence": r.confidence,
                }
                for r in new_rules
            ]
            self._rules.extend(rule_dicts)
            self._save_rules()

        log.info("ImproverDevice.improve: %d rules extracted from %d patterns", len(new_rules), len(clusters))
        return new_rules

    def get_rules(self) -> list[dict]:
        """Return all persisted improvement rules."""
        return list(self._rules)

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load_rules(self) -> None:
        rules_file = _RULES_DIR / "rules.json"
        if rules_file.exists():
            try:
                with open(rules_file) as f:
                    self._rules = json.load(f)
                log.info("ImproverDevice: loaded %d rules from disk", len(self._rules))
            except Exception as exc:
                log.warning("ImproverDevice: failed to load rules: %s", exc)

    def _save_rules(self) -> None:
        _RULES_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(_RULES_DIR / "rules.json", "w") as f:
                json.dump(self._rules, f, indent=2)
            log.info("ImproverDevice: saved %d rules to disk", len(self._rules))
        except Exception as exc:
            log.error("ImproverDevice: failed to save rules: %s", exc)
