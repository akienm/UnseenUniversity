"""Tests for devices/improver/device.py — ImproverDevice.

Criterion 4: same CriticJudgment input produces constructive suggestion from
Improver (optimism=+1) vs failure-focused verdict from Critic (optimism=-1).
This symmetry is verified by checking the system prompt each gets.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from devices.critic.agent import CriticJudgment, Decision, LearningRule
from devices.improver.device import ImproverDevice, _IMPROVEMENT_CRITERIA

# ── Helpers ───────────────────────────────────────────────────────────────────


def _fake_judgment(verdict: str = "bad", pattern: str = "error_not_recovered") -> CriticJudgment:
    decision = Decision(
        ticket_id="T-test",
        turn_num=1,
        decision_point="tool_selection",
        choice="Bash",
        context={},
        tool_result="ERROR: file not found",
    )
    return CriticJudgment(
        decision=decision,
        verdict=verdict,
        confidence=0.9,
        reasoning="Tool Bash failed: ERROR: file not found",
        pattern=pattern,
        improvement="Detect failures early",
    )


def _mock_evaluator_result(score: float = 0.8) -> dict:
    return {
        "judge_index": 0,
        "passed": score >= 0.6,
        "score": score,
        "criteria_results": [
            {"name": "identify_pattern", "passed": True, "reasoning": "The tool call pattern indicates repeated retries without checking failure state"},
            {"name": "suggest_improvement", "passed": True, "reasoning": "Check error output before retrying; switch to alternative tool after first failure"},
            {"name": "generalize_rule", "passed": True, "reasoning": "When a tool returns an error, do not retry the same tool — try an alternative"},
        ],
        "raw_response": "...",
    }


def _make_device(inference=None, tmp_rules_dir: Path | None = None):
    with patch("devices.improver.device._RULES_DIR", tmp_rules_dir or Path("/tmp/improver_test")):
        dev = ImproverDevice.__new__(ImproverDevice)
        dev._inference = inference
        dev._rules = []
        dev._errors = []
    return dev


# ── BaseDevice contract ───────────────────────────────────────────────────────


def test_who_am_i():
    dev = _make_device()
    info = dev.who_am_i()
    assert info["device_id"] == "improver"
    assert "version" in info


def test_capabilities():
    dev = _make_device()
    caps = dev.capabilities()
    assert "improve" in caps["mcp_tools"]


def test_health_healthy():
    dev = _make_device()
    assert dev.health()["status"] == "healthy"


def test_health_degraded_after_error():
    dev = _make_device()
    dev._errors.append("something failed")
    assert dev.health()["status"] == "degraded"


def test_startup_errors_empty():
    dev = _make_device()
    assert dev.startup_errors() == []


# ── improve() ────────────────────────────────────────────────────────────────


def test_improve_empty_returns_empty():
    dev = _make_device()
    result = dev.improve([])
    assert result == []


def test_improve_returns_learning_rules():
    mock_core = MagicMock()
    mock_core.evaluate.return_value = _mock_evaluator_result()

    dev = _make_device()

    with patch("devices.improver.device.EvaluatorCore", return_value=mock_core):
        rules = dev.improve([_fake_judgment()])

    assert len(rules) > 0
    # Each returned item should be a LearningRule with required fields
    for r in rules:
        assert hasattr(r, "pattern_name")
        assert hasattr(r, "condition")
        assert hasattr(r, "action")
        assert hasattr(r, "confidence")


def test_improve_calls_evaluatorcore_with_optimism_plus_one():
    mock_core = MagicMock()
    mock_core.evaluate.return_value = _mock_evaluator_result()

    dev = _make_device()

    with patch("devices.improver.device.EvaluatorCore", return_value=mock_core):
        dev.improve([_fake_judgment()])

    mock_core.evaluate.assert_called_once()
    call_kwargs = mock_core.evaluate.call_args
    optimism_val = call_kwargs[1].get("optimism") or call_kwargs[0][2]
    assert optimism_val == 1.0, f"Improver must call EvaluatorCore with optimism=+1.0, got {optimism_val}"


def test_improve_uses_improvement_criteria():
    mock_core = MagicMock()
    mock_core.evaluate.return_value = _mock_evaluator_result()

    dev = _make_device()

    with patch("devices.improver.device.EvaluatorCore", return_value=mock_core):
        dev.improve([_fake_judgment()])

    criteria_arg = mock_core.evaluate.call_args[0][1]
    assert criteria_arg == _IMPROVEMENT_CRITERIA


def test_improve_clusters_by_pattern():
    mock_core = MagicMock()
    mock_core.evaluate.return_value = _mock_evaluator_result()

    dev = _make_device()
    patterns = [
        _fake_judgment(pattern="error_not_recovered"),
        _fake_judgment(pattern="error_not_recovered"),  # same cluster
        _fake_judgment(pattern="slow_progress"),         # different cluster
    ]

    with patch("devices.improver.device.EvaluatorCore", return_value=mock_core):
        dev.improve(patterns)

    # Should have called EvaluatorCore once per distinct pattern
    assert mock_core.evaluate.call_count == 2


def test_improve_persists_rules(tmp_path):
    mock_core = MagicMock()
    mock_core.evaluate.return_value = _mock_evaluator_result()

    with patch("devices.improver.device._RULES_DIR", tmp_path):
        dev = ImproverDevice.__new__(ImproverDevice)
        dev._inference = MagicMock()
        dev._rules = []
        dev._errors = []

        with patch("devices.improver.device.EvaluatorCore", return_value=mock_core):
            dev.improve([_fake_judgment()])

        rules_file = tmp_path / "rules.json"
        assert rules_file.exists(), "Rules must be persisted to disk"
        data = json.loads(rules_file.read_text())
        assert len(data) > 0


def test_improve_rules_reloadable(tmp_path):
    mock_core = MagicMock()
    mock_core.evaluate.return_value = _mock_evaluator_result()

    with patch("devices.improver.device._RULES_DIR", tmp_path):
        dev1 = ImproverDevice.__new__(ImproverDevice)
        dev1._inference = MagicMock()
        dev1._rules = []
        dev1._errors = []

        with patch("devices.improver.device.EvaluatorCore", return_value=mock_core):
            dev1.improve([_fake_judgment()])

        n_rules = len(dev1.get_rules())

        # Reload — should pick up persisted rules
        dev2 = ImproverDevice.__new__(ImproverDevice)
        dev2._inference = None
        dev2._rules = []
        dev2._errors = []
        with patch("devices.improver.device._RULES_DIR", tmp_path):
            dev2._load_rules()

        assert len(dev2.get_rules()) == n_rules


def test_improve_non_fatal_on_core_error():
    mock_core = MagicMock()
    mock_core.evaluate.side_effect = RuntimeError("inference down")

    dev = _make_device()

    with patch("devices.improver.device.EvaluatorCore", return_value=mock_core):
        result = dev.improve([_fake_judgment()])

    # Should not raise — returns empty list and logs error
    assert result == []
    assert len(dev._errors) == 1


# ── Criterion 4: Improver constructive vs Critic fault-finding ───────────────


def test_criterion4_improver_gets_constructive_system_prompt():
    """Criterion 4: Improver uses optimism=+1; EvaluatorCore builds a constructive
    system prompt. Verified by checking that the system prompt built at optimism=+1
    contains constructive framing, not critical framing.
    """
    from devices.evaluator.core import _build_system

    critic_system = _build_system(-1.0)
    improver_system = _build_system(1.0)

    # Critic should contain fault-finding language
    assert any(word in critic_system.lower() for word in ("fault", "wrong", "fail")), (
        f"Critic system prompt at optimism=-1 should be fault-finding, got: {critic_system[:200]}"
    )

    # Improver should contain constructive language
    assert any(word in improver_system.lower() for word in ("constructive", "improve", "improvement")), (
        f"Improver system prompt at optimism=+1 should be constructive, got: {improver_system[:200]}"
    )

    # They must be different
    assert critic_system != improver_system, "Critic and Improver must use different system prompts"


def test_criterion4_same_input_different_optimism():
    """Criterion 4 end-to-end: same judgment → Improver calls EvaluatorCore at +1
    while CriticDevice calls EvaluatorCore at -1. The optimism values differ."""
    from devices.critic.device import CriticDevice

    judgment = _fake_judgment()
    captured = []

    def fake_evaluate(context, criteria, optimism=0.0, judge_index=0):
        captured.append(optimism)
        return {
            "judge_index": judge_index,
            "passed": optimism >= 0,
            "score": 0.5 + optimism * 0.2,
            "criteria_results": [
                {"name": "c", "passed": optimism >= 0, "reasoning": "reasoning here"}
            ],
            "raw_response": "...",
        }

    mock_core = MagicMock()
    mock_core.evaluate.side_effect = fake_evaluate

    imp_dev = _make_device()
    with patch("devices.improver.device.EvaluatorCore", return_value=mock_core):
        imp_dev.improve([judgment])

    with patch("devices.critic.device.EvaluatorCore", return_value=mock_core), \
         patch("devices.critic.device._RULES_DIR", Path("/tmp")):
        crit_dev = CriticDevice.__new__(CriticDevice)
        crit_dev._inference = MagicMock()
        crit_dev._agent = MagicMock()
        crit_dev._judgments = {}
        crit_dev._errors = []
        crit_dev.evaluate_decision(judgment.decision)

    # Improver called with +1.0, Critic called with -1.0
    assert 1.0 in captured, f"Improver must call EvaluatorCore with optimism=+1.0; got {captured}"
    assert -1.0 in captured, f"Critic must call EvaluatorCore with optimism=-1.0; got {captured}"
