"""Tests for devices/evaluator/core.py — EvaluatorCore with optimism parameter."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from unseen_university.devices.evaluator.core import EvaluatorCore, _build_system


# ── _build_system ─────────────────────────────────────────────────────────────


def test_build_system_critical_contains_wrong():
    system = _build_system(-1.0)
    assert "wrong" in system.lower() or "fail" in system.lower()
    assert "critical" in system.lower() or "failures" in system.lower()


def test_build_system_constructive_contains_improve():
    system = _build_system(+1.0)
    assert "improve" in system.lower() or "constructive" in system.lower()


def test_build_system_neutral_contains_impartial():
    system = _build_system(0.0)
    assert "impartial" in system.lower()


def test_build_system_different_for_extremes():
    critical = _build_system(-1.0)
    constructive = _build_system(+1.0)
    assert critical != constructive


def test_build_system_boundary_negative():
    """optimism=-0.5 is exactly at threshold — must produce critical stance."""
    system = _build_system(-0.5)
    assert "wrong" in system.lower() or "critical" in system.lower() or "failures" in system.lower()


def test_build_system_boundary_positive():
    """optimism=+0.5 is exactly at threshold — must produce constructive stance."""
    system = _build_system(+0.5)
    assert "improve" in system.lower() or "constructive" in system.lower()


def test_build_system_all_include_json_format():
    """Every optimism value must include the JSON response format instruction."""
    for opt in [-1.0, 0.0, +1.0]:
        system = _build_system(opt)
        assert "overall_passed" in system
        assert "criteria_results" in system


# ── EvaluatorCore.evaluate ────────────────────────────────────────────────────


def _mock_inference(response_text: str):
    inf = MagicMock()
    inf.dispatch.return_value = MagicMock(text=response_text)
    return inf


def _judge_resp(passed: bool = True) -> str:
    return json.dumps({
        "overall_passed": passed,
        "criteria_results": [{"name": "coherent", "passed": passed, "reasoning": "ok"}],
    })


def test_evaluate_returns_expected_shape():
    criteria = [{"name": "coherent", "instruction": "Is it coherent?"}]
    inf = _mock_inference(_judge_resp(True))
    core = EvaluatorCore(inference_device=inf)
    result = core.evaluate("hello world", criteria, optimism=0.0)
    assert "judge_index" in result
    assert "passed" in result
    assert "score" in result
    assert "criteria_results" in result
    assert "raw_response" in result


def test_evaluate_pass_result():
    criteria = [{"name": "coherent"}]
    inf = _mock_inference(_judge_resp(True))
    core = EvaluatorCore(inference_device=inf)
    result = core.evaluate("good output", criteria, optimism=0.0)
    assert result["passed"] is True
    assert result["score"] == 1.0


def test_evaluate_fail_result():
    criteria = [{"name": "coherent"}]
    inf = _mock_inference(_judge_resp(False))
    core = EvaluatorCore(inference_device=inf)
    result = core.evaluate("bad output", criteria, optimism=0.0)
    assert result["passed"] is False


def test_evaluate_uses_different_system_prompt_for_optimism():
    """Verify that critical vs constructive optimism produces different system prompts."""
    criteria = [{"name": "coherent", "instruction": "Is it coherent?"}]

    inf_critical = _mock_inference(_judge_resp(False))
    inf_constructive = _mock_inference(_judge_resp(True))

    core_c = EvaluatorCore(inference_device=inf_critical)
    core_i = EvaluatorCore(inference_device=inf_constructive)

    core_c.evaluate("some output", criteria, optimism=-1.0)
    core_i.evaluate("some output", criteria, optimism=+1.0)

    # Extract the system= kwarg from each dispatch call
    critical_call = inf_critical.dispatch.call_args
    constructive_call = inf_constructive.dispatch.call_args

    critical_req = critical_call[0][0]
    constructive_req = constructive_call[0][0]

    assert critical_req.system != constructive_req.system
    assert "wrong" in critical_req.system.lower() or "critical" in critical_req.system.lower() or "failures" in critical_req.system.lower()
    assert "improve" in constructive_req.system.lower() or "constructive" in constructive_req.system.lower()


def test_evaluate_error_produces_fail_entry_not_raise():
    """Inference errors must produce a failed entry, never raise."""
    criteria = [{"name": "coherent"}]
    inf = MagicMock()
    inf.dispatch.side_effect = RuntimeError("inference down")
    core = EvaluatorCore(inference_device=inf)
    result = core.evaluate("some output", criteria, optimism=0.0, judge_index=2)
    assert result["passed"] is False
    assert result["judge_index"] == 2
    assert "error" in result["raw_response"]


def test_evaluate_judge_index_propagated():
    criteria = [{"name": "coherent"}]
    inf = _mock_inference(_judge_resp(True))
    core = EvaluatorCore(inference_device=inf)
    result = core.evaluate("output", criteria, optimism=0.0, judge_index=7)
    assert result["judge_index"] == 7
