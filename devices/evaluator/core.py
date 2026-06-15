"""
EvaluatorCore — single-call evaluator with optimism bias.

The shared inference kernel that Critic, Evaluator, and Improver all
delegate to. The optimism parameter shifts the system prompt from
fault-finding (−1) through neutral (0) to improvement-seeking (+1).

Does NOT handle rubric loading or DB persistence — those are the
EvaluatorDevice's concern. Core is pure inference: context in, verdict out.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

_JSON_SHAPE = (
    '{"overall_passed": true, "criteria_results": '
    '[{"name": "...", "passed": true, "reasoning": "..."}]}'
)

_RESPONSE_FORMAT = (
    " Respond ONLY with valid JSON (no markdown, no prose):\n" + _JSON_SHAPE
)

_HAIKU_MODEL = "anthropic/claude-haiku-4-5-20251001"


def _build_system(optimism: float) -> str:
    """Build an evaluation system prompt biased by optimism.

    optimism=-1.0: critical — err toward finding what is wrong.
    optimism=0.0:  neutral — impartial score against criteria.
    optimism=+1.0: constructive — err toward improvement framing.
    """
    if optimism <= -0.5:
        stance = (
            "You are a critical evaluator focused on finding what is wrong. "
            "Be thorough in identifying failures, omissions, and weaknesses. "
            "When uncertain, err on the side of marking criteria as failed."
        )
    elif optimism >= 0.5:
        stance = (
            "You are a constructive evaluator focused on identifying what could be improved. "
            "Acknowledge what is working and frame failures as improvement opportunities. "
            "When uncertain, err on the side of marking criteria as passed."
        )
    else:
        stance = (
            "You are an impartial output evaluator. "
            "Score each criterion fairly based on evidence in the output."
        )
    return stance + _RESPONSE_FORMAT


def _extract_json(text: str) -> dict:
    """Parse JSON from LLM response, stripping markdown fences if present."""
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        inner = lines[1:-1] if len(lines) > 2 else lines
        s = "\n".join(inner).strip()
    return json.loads(s)


class EvaluatorCore:
    """Single-call evaluator. Criterion scoring with optimism-biased system prompt.

    Not responsible for rubric storage, eval_history persistence, or
    multi-judge orchestration — those belong in EvaluatorDevice.
    """

    def __init__(self, inference_device=None, model: str = _HAIKU_MODEL) -> None:
        self._inference = inference_device
        self._model = model

    def _get_inference(self):
        if self._inference is None:
            from devices.inference.device import InferenceDevice

            self._inference = InferenceDevice()
        return self._inference

    def evaluate(
        self,
        context: str,
        criteria: list[dict],
        optimism: float = 0.0,
        judge_index: int = 0,
    ) -> dict:
        """Run one evaluation call.

        Returns a judge-entry dict:
          {judge_index, passed, score, criteria_results, raw_response}

        Never raises — inference errors are captured as failed criteria.
        """
        from devices.inference.shim import InferenceRequest

        system = _build_system(optimism)
        criteria_text = "\n".join(
            f"- {c.get('name', 'criterion')}: {c.get('instruction', 'evaluate this criterion')}"
            for c in criteria
        )
        prompt = f"Output to evaluate:\n{context}\n\nRubric criteria:\n{criteria_text}"

        try:
            req = InferenceRequest(
                messages=[{"role": "user", "content": prompt}],
                system=system,
                model=self._model,
                max_tokens=1024,
                temperature=0.0,
            )
            resp = self._get_inference().dispatch(req)
            parsed = _extract_json(resp.text)
            overall_passed = bool(parsed.get("overall_passed", False))
            cr = parsed.get("criteria_results", [])
            if not cr:
                cr = [
                    {
                        "name": c.get("name", "criterion"),
                        "passed": overall_passed,
                        "reasoning": "no detail",
                    }
                    for c in criteria
                ]
            passed_count = sum(1 for item in cr if item.get("passed", False))
            score = passed_count / len(cr) if cr else 0.0
            return {
                "judge_index": judge_index,
                "passed": overall_passed,
                "score": round(score, 4),
                "criteria_results": cr,
                "raw_response": resp.text[:500],
            }
        except Exception as exc:
            log.warning("EvaluatorCore judge %d failed: %s", judge_index, exc)
            return {
                "judge_index": judge_index,
                "passed": False,
                "score": 0.0,
                "criteria_results": [
                    {
                        "name": c.get("name", "criterion"),
                        "passed": False,
                        "reasoning": f"judge {judge_index} error: {exc}",
                    }
                    for c in criteria
                ],
                "raw_response": f"error: {exc}",
            }
