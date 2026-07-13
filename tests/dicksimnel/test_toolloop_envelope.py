"""
tests/dicksimnel/test_toolloop_envelope.py — terminal-envelope parsing + availability guard.

The DS loop converged into the shared inference/agentic_loop.py. This covers:
  - parse_terminal_envelope(): JSON envelope, legacy DONE:/ESCALATE: prefixes, None cases
  - the no-live-source → LOOP_AVAILABILITY guard (money-safety: a source-down must NOT be
    read as a capability failure, or the domain walk would bump to a paid tier)
  - MAX_TURNS → LOOP_MAX_TURNS outcome
"""

from __future__ import annotations

from unittest.mock import MagicMock

from unseen_university.agentic.loop import (
    LOOP_AVAILABILITY,
    LOOP_DONE,
    LOOP_MAX_TURNS,
    AgenticLoop,
    NativeToolCodec,
    parse_terminal_envelope,
)


class TestParseTerminalResponse:
    def test_json_done_envelope(self):
        text = '{"status": "done", "result": "tests pass", "error_class": null, "error_number": null}'
        result = parse_terminal_envelope(text)
        assert result is not None
        assert result["status"] == "done"
        assert result["result"] == "tests pass"

    def test_json_escalate_envelope(self):
        text = '{"status": "escalate", "result": "HIGH-inertia file touched", "error_class": "ESCALATE", "error_number": null}'
        result = parse_terminal_envelope(text)
        assert result is not None
        assert result["status"] == "escalate"
        assert result["error_class"] == "ESCALATE"

    def test_json_error_envelope(self):
        text = '{"status": "error", "result": "something failed", "error_class": "MAX_TURNS", "error_number": 50}'
        result = parse_terminal_envelope(text)
        assert result is not None
        assert result["status"] == "error"
        assert result["error_number"] == 50

    def test_legacy_done_prefix(self):
        result = parse_terminal_envelope("DONE: tests pass, committed abc123")
        assert result is not None
        assert result["status"] == "done"
        assert "tests pass" in result["result"]

    def test_legacy_escalate_prefix(self):
        result = parse_terminal_envelope("ESCALATE: HIGH-inertia file requires approval")
        assert result is not None
        assert result["status"] == "escalate"
        assert result["error_class"] == "ESCALATE"

    def test_planning_prose_returns_none(self):
        result = parse_terminal_envelope(
            "I'll start by reading the current agentic_loop.py to understand the existing structure."
        )
        assert result is None

    def test_empty_returns_none(self):
        assert parse_terminal_envelope("") is None
        assert parse_terminal_envelope("   ") is None

    def test_invalid_json_returns_none(self):
        result = parse_terminal_envelope("{not valid json}")
        assert result is None

    def test_json_without_status_returns_none(self):
        result = parse_terminal_envelope('{"result": "something"}')
        assert result is None

    def test_whitespace_around_json(self):
        text = '\n  {"status": "done", "result": "ok", "error_class": null, "error_number": null}  \n'
        result = parse_terminal_envelope(text)
        assert result is not None
        assert result["status"] == "done"


def _resp(*, text, finish_reason="stop", source_kind="cloud", tool_calls=None,
          source_billing_type="usage_based", cost_estimate=0.0, input_tokens=0, output_tokens=0):
    r = MagicMock()
    r.text = text
    r.finish_reason = finish_reason
    r.source_kind = source_kind
    r.tool_calls = tool_calls
    r.source_billing_type = source_billing_type
    r.cost_estimate = cost_estimate
    r.input_tokens = input_tokens
    r.output_tokens = output_tokens
    r.model = "test-model"
    return r


class TestNoSourceAvailabilitySignal:
    """The load-bearing money-safety guard (T-router-failure-bump-escalation).

    A 'no live source' condition RETURNS an error InferenceResponse (finish_reason='error'/
    source_kind='none') — it does NOT raise. If the loop let that flow through as a model
    reply, the domain walk would see 'no DONE' and classify it CAPABILITY → bump to a pricier
    tier → PAID escalation because a source is down ('Hex-DOWN is not a branch'). This pins
    that the loop converts that error response to the LOOP_AVAILABILITY outcome.
    """

    def _run_with_response(self, response):
        device = MagicMock()
        device.dispatch.return_value = response
        loop = AgenticLoop(codec=NativeToolCodec(), max_turns=3, inference_device=device)
        return loop.run(system_prompt="sys", initial_message="do work", ticket_id="T-x")

    def test_no_source_error_response_returns_availability(self):
        resp = _resp(
            text="[InferenceDevice: no live inference source for task_class=worker]",
            finish_reason="error", source_kind="none",
        )
        assert self._run_with_response(resp).outcome == LOOP_AVAILABILITY

    def test_source_kind_none_alone_returns_availability(self):
        """source_kind='none' is sufficient to signal availability failure (defensive: either flag)."""
        resp = _resp(text="anything", finish_reason="stop", source_kind="none")
        assert self._run_with_response(resp).outcome == LOOP_AVAILABILITY

    def test_healthy_done_response_is_not_swallowed(self):
        """The guard is SPECIFIC — a live DONE response must NOT be misread as availability."""
        resp = _resp(
            text='{"status": "done", "result": "did the work"}',
            finish_reason="stop", source_kind="cloud",
        )
        result = self._run_with_response(resp)
        assert result.outcome == LOOP_DONE
        assert "done" in result.text


class TestMaxTurnsEnvelope:
    def test_max_turns_returns_max_turns_outcome(self):
        """Hitting the turn cap without a terminal → LOOP_MAX_TURNS."""
        resp = _resp(
            text=None,
            tool_calls=[{"id": "t1", "function": {"name": "Bash", "arguments": '{"command": "echo hi"}'}}],
        )
        device = MagicMock()
        device.dispatch.return_value = resp
        loop = AgenticLoop(codec=NativeToolCodec(), max_turns=1, inference_device=device)
        result = loop.run(system_prompt="test system", initial_message="begin", ticket_id="T-test")
        assert result.outcome == LOOP_MAX_TURNS
        assert "MAX_TURNS" in result.text
