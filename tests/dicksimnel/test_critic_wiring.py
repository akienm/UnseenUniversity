"""
Tests for CriticAgent wired into DickSimnel ToolLoop.

Tests:
- ToolLoop critic wiring: evaluate_decision called per tool call
- ToolLoop critic wiring: apply_rules advisory logged when rule fires
- ToolLoop critic wiring: learn_from_critic called at sprint end (saves rules)
- ToolLoop critic wiring: critic failure is non-fatal (sprint completes)
- CriticAgent.load_rules: handles missing file gracefully (already guarded in device)
- CriticAgent.load_rules: skips malformed rule entries without raising
- CriticDevice._save_rules / _load_rules: round-trips rules to disk
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from devices.critic.agent import CriticAgent, Decision, LearningRule
from devices.critic.device import CriticDevice


# ── CriticAgent.load_rules ─────────────────────────────────────────────────────

def test_load_rules_accepts_valid_data():
    agent = CriticAgent()
    agent.load_rules([
        {"pattern": "error_not_recovered", "condition": "tool returns error",
         "action": "try alternative", "confidence": 0.85},
    ])
    assert len(agent.export_rules()) == 1


def test_load_rules_skips_malformed_entries(caplog):
    import logging
    agent = CriticAgent()
    with caplog.at_level(logging.WARNING, logger="devices.critic.agent"):
        agent.load_rules([
            {"pattern": "ok", "condition": "c", "action": "a", "confidence": 0.5},
            {"pattern": "bad_no_condition"},  # missing keys
            None,  # not a dict
        ])
    assert len(agent.export_rules()) == 1
    assert "malformed" in caplog.text.lower()


def test_load_rules_empty_list_is_noop():
    agent = CriticAgent()
    agent.load_rules([])
    assert agent.export_rules() == []


# ── CriticDevice persistence ───────────────────────────────────────────────────

def test_critic_device_save_load_round_trip(tmp_path):
    with patch("devices.critic.device._RULES_DIR", tmp_path):
        dev = CriticDevice()
        dev._agent.learn_from_patterns({
            "failure_modes": ["error_not_recovered"],
            "common_patterns": {},
            "improvement_opportunities": [],
        })
        dev._save_rules()
        rules_file = tmp_path / "rules.json"
        assert rules_file.exists()

        # Load a fresh device — should pick up persisted rules
        dev2 = CriticDevice()
        assert len(dev2._agent.export_rules()) == len(dev._agent.export_rules())


def test_critic_device_load_missing_file_is_noop(tmp_path):
    with patch("devices.critic.device._RULES_DIR", tmp_path):
        dev = CriticDevice()  # no rules.json → should not raise
    assert dev._agent.export_rules() == []


# ── ToolLoop critic wiring ─────────────────────────────────────────────────────

def _minimal_response(tool_name: str = "Bash", tool_arg: str = "ls", text: str = "") -> MagicMock:
    """Build a minimal InferenceDevice response mock with one tool call."""
    tc = {
        "id": "tc_001",
        "function": {"name": tool_name, "arguments": json.dumps({"command": tool_arg})},
    }
    resp = MagicMock()
    resp.text = text
    resp.tool_calls = [tc]
    resp.cost_estimate = 0.0
    resp.source_billing_type = "usage_based"
    return resp


def _done_response(text: str = '{"status":"done","result":"ok","error_class":null,"error_number":null}') -> MagicMock:
    resp = MagicMock()
    resp.text = text
    resp.tool_calls = None
    resp.cost_estimate = 0.0
    resp.source_billing_type = "usage_based"
    return resp


def test_toolloop_calls_evaluate_decision_per_tool_call():
    """After each tool call, CriticAgent.evaluate_decision is called."""
    from devices.dicksimnel.toolloop import ToolLoop

    responses = [_minimal_response("Bash", "ls"), _done_response()]
    tool_result = "file1.py\n"

    mock_agent_eval = MagicMock(return_value=MagicMock(
        verdict="good", confidence=0.9, pattern="ok", improvement=None))

    with patch("devices.inference.device.InferenceDevice") as mock_inf_cls, \
         patch("devices.dicksimnel.toolloop._execute_tool", return_value=tool_result), \
         patch("devices.dicksimnel.toolloop._orientation_prefix", return_value=""), \
         patch("devices.critic.device.CriticDevice._load_rules"), \
         patch("devices.critic.agent.CriticAgent.evaluate_decision", mock_agent_eval), \
         patch("devices.critic.agent.CriticAgent.analyze_pattern", return_value={
             "verdict_distribution": {}, "common_patterns": {}, "failure_modes": [],
             "failure_count": 0, "improvement_opportunities": [],
         }), \
         patch("devices.critic.device.CriticDevice._save_rules"):
        mock_inf = MagicMock()
        mock_inf_cls.return_value = mock_inf
        mock_inf.dispatch.side_effect = responses

        tl = ToolLoop(max_turns=5)
        tl.run({"id": "T-test", "title": "t", "tags": [], "description": "d"}, "sys")

    mock_agent_eval.assert_called_once()
    call_args = mock_agent_eval.call_args[0][0]
    assert call_args.ticket_id == "T-test"
    assert call_args.choice == "Bash"


def test_toolloop_logs_critic_advisory_when_rule_fires(caplog):
    """When a critic rule matches the current tool context, an advisory is logged at INFO."""
    import logging
    from devices.dicksimnel.toolloop import ToolLoop

    responses = [_minimal_response("Bash", "ls"), _done_response()]

    with patch("devices.inference.device.InferenceDevice") as mock_inf_cls, \
         patch("devices.dicksimnel.toolloop._execute_tool", return_value="ok"), \
         patch("devices.dicksimnel.toolloop._orientation_prefix", return_value=""), \
         patch("devices.critic.device.CriticDevice._load_rules"), \
         patch("devices.critic.device.CriticDevice.get_recommendation",
               return_value={"action": "try different tool", "confidence": 0.85,
                             "rule": "error_not_recovered"}), \
         patch("devices.critic.agent.CriticAgent.evaluate_decision", return_value=MagicMock(
             verdict="neutral", confidence=0.5, pattern=None, improvement=None)), \
         patch("devices.critic.agent.CriticAgent.analyze_pattern", return_value={
             "verdict_distribution": {}, "common_patterns": {}, "failure_modes": [],
             "failure_count": 0, "improvement_opportunities": [],
         }), \
         patch("devices.critic.device.CriticDevice._save_rules"):
        mock_inf = MagicMock()
        mock_inf_cls.return_value = mock_inf
        mock_inf.dispatch.side_effect = responses

        tl = ToolLoop(max_turns=5)
        with caplog.at_level(logging.INFO, logger="devices.dicksimnel.toolloop"):
            tl.run({"id": "T-adv", "title": "t", "tags": [], "description": "d"}, "sys")

    assert "Critic advisory" in caplog.text
    assert "error_not_recovered" in caplog.text


def test_toolloop_saves_rules_at_sprint_end():
    """_save_rules is called when critic has judgments after sprint completes."""
    from devices.dicksimnel.toolloop import ToolLoop

    responses = [_minimal_response("Bash", "ls"), _done_response()]

    save_mock = MagicMock()
    with patch("devices.inference.device.InferenceDevice") as mock_inf_cls, \
         patch("devices.dicksimnel.toolloop._execute_tool", return_value="ok"), \
         patch("devices.dicksimnel.toolloop._orientation_prefix", return_value=""), \
         patch("devices.critic.device.CriticDevice._load_rules"), \
         patch("devices.critic.agent.CriticAgent.evaluate_decision", return_value=MagicMock(
             verdict="good", confidence=0.9, pattern="ok", improvement=None)), \
         patch("devices.critic.agent.CriticAgent.analyze_pattern", return_value={
             "verdict_distribution": {"good": 1}, "common_patterns": {}, "failure_modes": [],
             "failure_count": 0, "improvement_opportunities": [],
         }), \
         patch("devices.critic.device.CriticDevice._save_rules", save_mock):
        mock_inf = MagicMock()
        mock_inf_cls.return_value = mock_inf
        mock_inf.dispatch.side_effect = responses

        tl = ToolLoop(max_turns=5)
        tl.run({"id": "T-save", "title": "t", "tags": [], "description": "d"}, "sys")

    save_mock.assert_called_once()


def test_toolloop_continues_when_critic_unavailable():
    """Sprint completes even if CriticDevice raises on import."""
    from devices.dicksimnel.toolloop import ToolLoop

    responses = [_minimal_response("Bash", "ls"), _done_response()]

    with patch("devices.inference.device.InferenceDevice") as mock_inf_cls, \
         patch("devices.dicksimnel.toolloop._execute_tool", return_value="ok"), \
         patch("devices.dicksimnel.toolloop._orientation_prefix", return_value=""), \
         patch("devices.critic.device.CriticDevice.__init__",
               side_effect=RuntimeError("critic down")):
        mock_inf = MagicMock()
        mock_inf_cls.return_value = mock_inf
        mock_inf.dispatch.side_effect = responses

        tl = ToolLoop(max_turns=5)
        result = tl.run({"id": "T-nocrit", "title": "t", "tags": [], "description": "d"}, "sys")

    # Sprint must complete — critic failure is non-fatal
    assert result is not None
