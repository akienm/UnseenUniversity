"""
tests/dicksimnel/test_toolloop_envelope.py — JSON envelope parsing in ToolLoop.

Covers _parse_terminal_response():
  - JSON envelope (new protocol)
  - Legacy DONE: prefix (backwards compat)
  - Legacy ESCALATE: prefix
  - Planning-mode prose returns None
  - Empty text returns None
  - MAX_TURNS return is valid JSON
"""

from __future__ import annotations

import json
import pytest

from devices.dicksimnel.toolloop import _parse_terminal_response, ToolLoop


class TestParseTerminalResponse:
    def test_json_done_envelope(self):
        text = '{"status": "done", "result": "tests pass", "error_class": null, "error_number": null}'
        result = _parse_terminal_response(text)
        assert result is not None
        assert result["status"] == "done"
        assert result["result"] == "tests pass"

    def test_json_escalate_envelope(self):
        text = '{"status": "escalate", "result": "HIGH-inertia file touched", "error_class": "ESCALATE", "error_number": null}'
        result = _parse_terminal_response(text)
        assert result is not None
        assert result["status"] == "escalate"
        assert result["error_class"] == "ESCALATE"

    def test_json_error_envelope(self):
        text = '{"status": "error", "result": "something failed", "error_class": "MAX_TURNS", "error_number": 50}'
        result = _parse_terminal_response(text)
        assert result is not None
        assert result["status"] == "error"
        assert result["error_number"] == 50

    def test_legacy_done_prefix(self):
        result = _parse_terminal_response("DONE: tests pass, committed abc123")
        assert result is not None
        assert result["status"] == "done"
        assert "tests pass" in result["result"]

    def test_legacy_escalate_prefix(self):
        result = _parse_terminal_response("ESCALATE: HIGH-inertia file requires approval")
        assert result is not None
        assert result["status"] == "escalate"
        assert result["error_class"] == "ESCALATE"

    def test_planning_prose_returns_none(self):
        result = _parse_terminal_response(
            "I'll start by reading the current toolloop.py to understand the existing structure."
        )
        assert result is None

    def test_empty_returns_none(self):
        assert _parse_terminal_response("") is None
        assert _parse_terminal_response("   ") is None

    def test_invalid_json_returns_none(self):
        result = _parse_terminal_response("{not valid json}")
        assert result is None

    def test_json_without_status_returns_none(self):
        result = _parse_terminal_response('{"result": "something"}')
        assert result is None

    def test_whitespace_around_json(self):
        text = '\n  {"status": "done", "result": "ok", "error_class": null, "error_number": null}  \n'
        result = _parse_terminal_response(text)
        assert result is not None
        assert result["status"] == "done"


class TestMaxTurnsEnvelope:
    def test_max_turns_returns_json(self):
        """MAX_TURNS return value is now a JSON error envelope."""
        from unittest.mock import patch, MagicMock
        from devices.inference.shim import InferenceResponse

        # Simulate 2 turns of tool calls then max_turns exceeded
        mock_response = MagicMock(spec=InferenceResponse)
        mock_response.text = None
        mock_response.tool_calls = [{"id": "t1", "function": {"name": "Bash", "arguments": '{"command": "echo hi"}'}}]
        mock_response.cost_estimate = 0.0
        mock_response.source_billing_type = "usage_based"

        loop = ToolLoop(max_turns=1)
        with patch("devices.inference.device.InferenceDevice") as MockDevice:
            MockDevice.return_value.dispatch.return_value = mock_response
            result = loop.run({"id": "T-test", "title": "test", "tags": [], "description": "test"}, "test system")

        assert result is not None
        envelope = json.loads(result)
        assert envelope["status"] == "error"
        assert envelope["error_class"] == "MAX_TURNS"
        assert isinstance(envelope["error_number"], int)
