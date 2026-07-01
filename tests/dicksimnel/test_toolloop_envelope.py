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

from unseen_university.devices.dicksimnel.toolloop import _parse_terminal_response, ToolLoop


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


class TestNoSourceAvailabilitySignal:
    """The load-bearing money-safety guard (T-router-failure-bump-escalation).

    After the pin-gate/cost-record work, a 'no live source' condition RETURNS an error
    InferenceResponse (finish_reason='error'/source_kind='none') — it does NOT raise. If
    ToolLoop.run let that response flow through as if it were a model reply, the DS driver
    would see 'no DONE' and classify it as a CAPABILITY failure → bump to a pricier tier →
    PAID escalation because a source is down ('Hex-DOWN is not a branch'). This pins the one
    line (toolloop.py) that converts that error response to the None availability signal.
    """

    def _run_with_response(self, response):
        from unittest.mock import patch
        loop = ToolLoop(max_turns=3)
        ticket = {"id": "T-x", "title": "t", "tags": [], "description": "d"}
        with patch("unseen_university.devices.inference.device.InferenceDevice") as MockDevice:
            MockDevice.return_value.dispatch.return_value = response
            return loop.run(ticket, "sys")

    def test_no_source_error_response_returns_none(self):
        """A no-source error response → None (AVAILABILITY), so the driver re-selects, never bumps."""
        from unseen_university.devices.inference.shim import InferenceResponse
        resp = InferenceResponse(
            text="[InferenceDevice: no live inference source for task_class=worker]",
            finish_reason="error", source_kind="none", tool_calls=None,
        )
        assert self._run_with_response(resp) is None

    def test_source_kind_none_alone_returns_none(self):
        """source_kind='none' is sufficient to signal availability failure (defensive: either flag)."""
        from unseen_university.devices.inference.shim import InferenceResponse
        resp = InferenceResponse(text="anything", finish_reason="stop", source_kind="none", tool_calls=None)
        assert self._run_with_response(resp) is None

    def test_healthy_done_response_is_not_swallowed(self):
        """The guard is SPECIFIC — a live DONE response must NOT be misread as availability."""
        from unseen_university.devices.inference.shim import InferenceResponse
        resp = InferenceResponse(
            text='{"status": "done", "result": "did the work"}',
            finish_reason="stop", source_kind="cloud", tool_calls=None,
        )
        result = self._run_with_response(resp)
        assert result is not None and "done" in result


class TestMaxTurnsEnvelope:
    def test_max_turns_returns_json(self):
        """MAX_TURNS return value is now a JSON error envelope."""
        from unittest.mock import patch, MagicMock
        from unseen_university.devices.inference.shim import InferenceResponse

        # Simulate 2 turns of tool calls then max_turns exceeded
        mock_response = MagicMock(spec=InferenceResponse)
        mock_response.text = None
        mock_response.tool_calls = [{"id": "t1", "function": {"name": "Bash", "arguments": '{"command": "echo hi"}'}}]
        mock_response.cost_estimate = 0.0
        mock_response.source_billing_type = "usage_based"

        loop = ToolLoop(max_turns=1)
        with patch("unseen_university.devices.inference.device.InferenceDevice") as MockDevice:
            MockDevice.return_value.dispatch.return_value = mock_response
            result = loop.run({"id": "T-test", "title": "test", "tags": [], "description": "test"}, "test system")

        assert result is not None
        envelope = json.loads(result)
        assert envelope["status"] == "error"
        assert envelope["error_class"] == "MAX_TURNS"
        assert isinstance(envelope["error_number"], int)
