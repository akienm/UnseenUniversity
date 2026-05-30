"""
EvaluatorShim — lifecycle shim for the evaluator rack device.

The evaluator has no external process to manage; the shim is a no-op
lifecycle wrapper that satisfies the BaseShim contract.

Bus envelope types:
  EvalResult  — return value of evaluate()
  RubricDef   — rubric as stored / returned from rubric_list()
"""

from __future__ import annotations

from dataclasses import dataclass, field

from unseen_university.shim import BaseShim


@dataclass
class EvalResult:
    """Return envelope for evaluate()."""

    eval_id: str
    agent_id: str
    rubric_id: str
    score: float  # 0.0 – 1.0
    verdict: str  # "pass" | "fail"
    judge_reasoning: list[dict]  # exactly 3 entries, one per judge
    evaluated_at: str  # ISO timestamp


@dataclass
class RubricDef:
    """A stored rubric, as returned by rubric_list()."""

    rubric_id: str
    name: str
    criteria: list[dict] = field(default_factory=list)
    updated_at: str = ""


class EvaluatorShim(BaseShim):
    """No external process — shim satisfies contract and does nothing."""

    @property
    def device_id(self) -> str:
        return "evaluator"

    def start(self) -> bool:
        return True

    def stop(self) -> bool:
        return True

    def restart(self) -> bool:
        return True

    def self_test(self) -> dict:
        return {"passed": True, "details": "no external process"}

    def rollback(self) -> None:
        pass
