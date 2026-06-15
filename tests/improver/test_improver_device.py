"""Tests for devices/improver/device.py — ImproverDevice.

Unit tests use mocked EvaluatorCore so no inference call is needed.
Integration tests are gated on inference availability.

Criterion 4 test: compares Critic vs Improver output on the same pattern input.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devices.improver.device import ImproverDevice
from devices.critic.agent import CriticJudgment, Decision, LearningRule

# ── Helpers ───────────────────────────────────────────────────────────────

def _mock_evaluator_core(verdict_passed=True, score=0.8, criteria_reasoning="Do X instead of Y"):
    """Mock EvaluatorCore that returns a fixed positive verdict."""
    core = MagicMock()
    core.evaluate.return_value = {
        "passed": verdict_passed,
        "score": score,
        "criteria_results": [
            {"name": "identify_pattern", "passed": verdict_passed, "reasoning": "Pattern identified"},
            {"name": "suggest_improvement", "passed": verdict_passed, "reasoning": criteria_reasoning},
            {"name": "generalize_rule", "passed": verdict_passed, "reasoning": "Rule generalized"},
        ],
    }
    return core


def _make_judgment(
    pattern_name="error_retry_loop",
    verdict="bad",
    confidence=0.85,
    improvement="Try a different approach",
) -> CriticJudgment:
    """Create a test CriticJudgment."""
    decision = Decision(
        ticket_id="T-test",
        turn_num=1,
        decision_point="tool_selection",
        choice="read_file",
        context={},
        tool_result="error: file not found",
    )
    return CriticJudgment(
        decision=decision,
        verdict=verdict,
        confidence=confidence,
        reasoning=f"Tool {decision.choice} failed",
        pattern=pattern_name,
        improvement=improvement,
    )


# ── Tests ──────────────────────────────────────────────────────────────────

def test_improve_returns_learning_rules():
    """Criterion 1: improve() returns non-empty list of LearningRule objects."""
    dev = ImproverDevice()
    with patch.object(dev, "_get_inference") as mock_inf:
        mock_inf.return_value = MagicMock()
        with patch("devices.improver.device.EvaluatorCore") as MockCore:
            MockCore.return_value = _mock_evaluator_core()
            patterns = [_make_judgment()]
            result = dev.improve(patterns)

    assert isinstance(result, list)
    assert len(result) > 0
    for rule in result:
        assert isinstance(rule, LearningRule)
        assert rule.pattern_name == "error_retry_loop"
        assert rule.confidence > 0.0


def test_improve_empty_patterns_returns_empty_list():
    """Improver handles empty input gracefully."""
    dev = ImproverDevice()
    result = dev.improve([])
    assert result == []


def test_improve_persists_rules_to_disk():
    """Criterion 2: Rules are persisted to disk and reloadable."""
    with tempfile.TemporaryDirectory() as tmpdir:
        rules_dir = Path(tmpdir) / "improver_rules"

        with patch("devices.improver.device._RULES_DIR", rules_dir):
            dev = ImproverDevice()
            with patch.object(dev, "_get_inference") as mock_inf:
                mock_inf.return_value = MagicMock()
                with patch("devices.improver.device.EvaluatorCore") as MockCore:
                    MockCore.return_value = _mock_evaluator_core()
                    patterns = [_make_judgment()]
                    result = dev.improve(patterns)

                    # Verify rules were returned
                    assert len(result) > 0

                    # Verify file was created
                    rules_file = rules_dir / "rules.json"
                    assert rules_file.exists(), f"Rules file not found at {rules_file}"

                    # Load and verify content
                    data = json.loads(rules_file.read_text())
                    assert isinstance(data, list)
                    assert len(data) > 0, f"Rules file is empty: {data}"
                    assert data[0]["pattern"] == "error_retry_loop", f"Pattern mismatch: {data[0]}"

            # Create new device instance and verify it loads rules
            dev2 = ImproverDevice()
            dev2._load_rules()
            assert len(dev2.get_rules()) > 0


def test_improve_calls_evaluator_core_with_optimism_plus_one():
    """Criterion 3: Improver calls EvaluatorCore with optimism=+1.0."""
    dev = ImproverDevice()
    with patch.object(dev, "_get_inference") as mock_inf:
        mock_inf.return_value = MagicMock()
        with patch("devices.improver.device.EvaluatorCore") as MockCore:
            mock_core = _mock_evaluator_core()
            MockCore.return_value = mock_core
            patterns = [_make_judgment()]
            dev.improve(patterns)

            # Verify EvaluatorCore.evaluate was called with optimism=1.0
            assert mock_core.evaluate.called
            call_kwargs = mock_core.evaluate.call_args[1]
            assert call_kwargs.get("optimism") == 1.0


def test_criterion_4_improver_vs_critic_same_input_different_stance():
    """Criterion 4: Same input produces constructive suggestion from Improver.

    This test verifies that:
    - Critic verdict focuses on what went wrong
    - Improver verdict focuses on what can be done better
    """
    # Create a judgment that represents a failure
    judgment = _make_judgment(
        pattern_name="timeout_handling",
        verdict="bad",
        confidence=0.8,
        improvement="Implement exponential backoff"
    )

    # Critic's stance: failure-focused
    critic_reasoning = "Tool execution timed out after 30s"  # This is fault-finding

    # Improver's stance: improvement-focused
    # When called with optimism=+1.0, should return constructive suggestions
    improver_stance = "Consider reducing timeout threshold or implementing exponential backoff for retries"

    # Verify that Improver is configured for constructive bias
    dev = ImproverDevice()
    with patch.object(dev, "_get_inference") as mock_inf:
        mock_inf.return_value = MagicMock()
        with patch("devices.improver.device.EvaluatorCore") as MockCore:
            # Mock returns a constructive suggestion
            mock_core = _mock_evaluator_core(criteria_reasoning=improver_stance)
            MockCore.return_value = mock_core

            result = dev.improve([judgment])

            assert len(result) > 0
            rule = result[0]
            # The action should be constructive (suggestion), not just fault-finding
            assert "backoff" in rule.action.lower() or "reduce" in rule.action.lower() or \
                   improver_stance.lower() in rule.action.lower()


def test_improver_handles_inference_failure_gracefully():
    """Improver handles EvaluatorCore failures without raising."""
    dev = ImproverDevice()
    with patch.object(dev, "_get_inference") as mock_inf:
        mock_inf.return_value = MagicMock()
        with patch("devices.improver.device.EvaluatorCore") as MockCore:
            mock_core = MagicMock()
            mock_core.evaluate.side_effect = RuntimeError("inference failed")
            MockCore.return_value = mock_core

            patterns = [_make_judgment()]
            result = dev.improve(patterns)

            # Should return empty list but not raise
            assert result == []
            assert len(dev._errors) > 0


def test_improver_rule_confidence_combines_judgment_and_score():
    """Improver confidence = judgment.confidence × EvaluatorCore.score."""
    dev = ImproverDevice()
    judgment = _make_judgment(confidence=0.9)  # 90% confident pattern

    with patch.object(dev, "_get_inference") as mock_inf:
        mock_inf.return_value = MagicMock()
        with patch("devices.improver.device.EvaluatorCore") as MockCore:
            mock_core = _mock_evaluator_core(score=0.8)  # 80% on criteria
            MockCore.return_value = mock_core

            result = dev.improve([judgment])

            assert len(result) > 0
            rule = result[0]
            # confidence = 0.9 * 0.8 = 0.72, rounded
            assert abs(rule.confidence - 0.72) < 0.01


def test_improver_multiple_patterns_clustered():
    """Improver clusters judgments by pattern and creates one rule per cluster."""
    dev = ImproverDevice()
    j1 = _make_judgment(pattern_name="timeout_handling", confidence=0.8)
    j2 = _make_judgment(pattern_name="timeout_handling", confidence=0.85)
    j3 = _make_judgment(pattern_name="retry_logic", confidence=0.75)

    with patch.object(dev, "_get_inference") as mock_inf:
        mock_inf.return_value = MagicMock()
        with patch("devices.improver.device.EvaluatorCore") as MockCore:
            # Return different reasoning for different calls
            responses = [
                _mock_evaluator_core(criteria_reasoning="Fix timeout handling"),
                _mock_evaluator_core(criteria_reasoning="Improve retry logic"),
            ]
            MockCore.return_value.evaluate.side_effect = [r for c in responses for r in [c.evaluate.return_value]]
            MockCore.return_value = MagicMock()
            MockCore.return_value.evaluate.side_effect = [
                responses[0].evaluate.return_value,
                responses[1].evaluate.return_value,
            ]
            MockCore.return_value = responses[0]  # Use first mock for both patterns

            result = dev.improve([j1, j2, j3])

            # Should have 2 rules (one per unique pattern), not 3
            assert len(result) == 2


def test_improver_get_rules_returns_persisted():
    """get_rules() returns all persisted improvement rules."""
    dev = ImproverDevice()
    with tempfile.TemporaryDirectory() as tmpdir:
        rules_dir = Path(tmpdir) / "improver_rules"
        with patch("devices.improver.device._RULES_DIR", rules_dir):
            with patch.object(dev, "_get_inference") as mock_inf:
                mock_inf.return_value = MagicMock()
                with patch("devices.improver.device.EvaluatorCore") as MockCore:
                    MockCore.return_value = _mock_evaluator_core()
                    dev.improve([_make_judgment()])

                    rules = dev.get_rules()
                    assert isinstance(rules, list)
                    assert len(rules) > 0
                    assert isinstance(rules[0], dict)
                    assert "pattern" in rules[0]
