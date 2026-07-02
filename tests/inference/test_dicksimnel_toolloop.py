"""Tests for the shared AgenticLoop native codec (was DS ToolLoop — converged in
D-domain-object-encapsulation). Covers the native OpenAI tool-calling turn-runner, the
shared tool executor, and _parse_response in the inference device."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.inference.agentic_loop import (
    LOOP_AVAILABILITY,
    LOOP_DONE,
    LOOP_MAX_TURNS,
    AgenticLoop,
    NativeToolCodec,
    TOOL_DEFINITIONS,
    _REPO_ROOT,
    execute_tool,
)


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_mock_response(text: str, tool_calls=None, tokens: int = 100) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.tool_calls = tool_calls
    r.input_tokens = 50
    r.output_tokens = tokens
    r.cost_estimate = 0.001
    r.finish_reason = "stop"
    r.source_kind = "cloud"
    r.source_billing_type = "usage_based"
    r.model = "qwen/qwen3-coder-30b"
    return r


def _bash_call(command: str, call_id: str = "call_abc") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "Bash", "arguments": json.dumps({"command": command})},
    }


def _loop_with(responses_or_fn, *, max_turns: int = 50):
    """Build an AgenticLoop (native codec) driven by a mock InferenceDevice.

    Returns (loop, device). Pass a list of responses (consumed in order) or a callable
    dispatch(req) side-effect. The mock device is injected, so no patching is needed.
    """
    device = MagicMock()
    device.dispatch.side_effect = responses_or_fn
    loop = AgenticLoop(codec=NativeToolCodec(), max_turns=max_turns, inference_device=device)
    return loop, device


def _run(loop, ticket_id="T", system="system"):
    return loop.run(system_prompt=system, initial_message="Work the ticket.", ticket_id=ticket_id)


# ── _parse_response extracts tool_calls (inference device) ────────────────────


def test_parse_response_extracts_tool_calls():
    from unseen_university.devices.inference.device import _parse_response

    raw = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_xyz",
                        "type": "function",
                        "function": {"name": "Bash", "arguments": '{"command": "ls -la"}'},
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }],
        "model": "qwen/qwen3-coder-30b",
        "usage": {"prompt_tokens": 50, "completion_tokens": 20},
    }
    resp = _parse_response(raw)
    assert resp.tool_calls is not None
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0]["function"]["name"] == "Bash"
    assert resp.finish_reason == "tool_calls"


def test_parse_response_no_tool_calls_stays_none():
    from unseen_university.devices.inference.device import _parse_response

    raw = {
        "choices": [{
            "message": {"role": "assistant", "content": "DONE: all good"},
            "finish_reason": "stop",
        }],
        "model": "qwen/qwen3-coder-30b",
        "usage": {},
    }
    resp = _parse_response(raw)
    assert resp.tool_calls is None
    assert resp.text == "DONE: all good"


# ── execute_tool (shared) ─────────────────────────────────────────────────────


def test_execute_bash_returns_output():
    result = execute_tool("Bash", {"command": "echo toolloop_test"}, _REPO_ROOT)
    assert "toolloop_test" in result


def test_execute_bash_denylist_blocks_rm_rf():
    result = execute_tool("Bash", {"command": "rm -rf /tmp/test"}, _REPO_ROOT)
    assert "ERROR" in result
    assert "denylist" in result


def test_execute_bash_denylist_blocks_force_push():
    result = execute_tool("Bash", {"command": "git push --force origin main"}, _REPO_ROOT)
    assert "ERROR" in result


def test_execute_read_existing_file(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("def hello(): pass")
    result = execute_tool("Read", {"path": str(f)}, _REPO_ROOT)
    assert "def hello" in result


def test_execute_read_missing_file():
    result = execute_tool("Read", {"path": "/nonexistent/path/file.py"}, _REPO_ROOT)
    assert "ERROR" in result
    assert "not found" in result


def test_execute_edit_replaces_text(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def old_name(): pass\n")
    result = execute_tool("Edit", {"file_path": str(f), "old_string": "old_name", "new_string": "new_name"}, _REPO_ROOT)
    assert "OK" in result
    assert "new_name" in f.read_text()


def test_execute_edit_fails_on_ambiguous_match(tmp_path):
    f = tmp_path / "dup.py"
    f.write_text("foo\nfoo\n")
    result = execute_tool("Edit", {"file_path": str(f), "old_string": "foo", "new_string": "bar"}, _REPO_ROOT)
    assert "ERROR" in result
    assert "2" in result


def test_execute_write_creates_file(tmp_path):
    new_file = tmp_path / "new.py"
    result = execute_tool("Write", {"file_path": str(new_file), "content": "x = 1\n"}, _REPO_ROOT)
    assert "OK" in result
    assert new_file.read_text() == "x = 1\n"


def test_execute_unknown_tool_returns_error():
    result = execute_tool("Teleport", {"destination": "somewhere"}, _REPO_ROOT)
    assert "ERROR" in result
    assert "unknown tool" in result


# ── AgenticLoop.run (native codec) ────────────────────────────────────────────


def test_loop_done_on_first_turn():
    """Model returns no tool_calls on turn 1 with a DONE envelope — completes immediately."""
    loop, _ = _loop_with([_make_mock_response("DONE: fixed it")])
    result = _run(loop, "T-1")
    assert result.outcome == LOOP_DONE
    assert "DONE" in result.text


def test_loop_multi_turn_bash_then_done():
    """Tool call on turn 1 → result injected as role:tool → done on turn 2."""
    tc = _bash_call("echo hello", "call_1")
    responses = [
        _make_mock_response("", tool_calls=[tc]),
        _make_mock_response("DONE: ran hello"),
    ]
    dispatch_calls = []

    def mock_dispatch(req):
        dispatch_calls.append(req)
        return responses.pop(0)

    loop, _ = _loop_with(mock_dispatch)
    result = _run(loop, "T-2")

    assert len(dispatch_calls) == 2
    second_messages = dispatch_calls[1].messages
    tool_messages = [m for m in second_messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_1"
    assert "hello" in tool_messages[0]["content"]
    assert result.outcome == LOOP_DONE
    assert "DONE" in result.text


def test_loop_tool_result_uses_role_tool():
    """Tool results land as role:tool messages, not role:user."""
    tc = _bash_call("echo ping", "call_ping")
    responses = [
        _make_mock_response("", tool_calls=[tc]),
        _make_mock_response("DONE: done"),
    ]
    dispatch_calls = []

    def mock_dispatch(req):
        dispatch_calls.append(req)
        return responses.pop(0)

    loop, _ = _loop_with(mock_dispatch)
    _run(loop, "T-r")

    second_messages = dispatch_calls[1].messages
    roles = [m["role"] for m in second_messages]
    assert "tool" in roles
    assert "user" not in roles[2:]  # after the initial user turn, no more user messages


def test_loop_sends_tool_definitions_in_request():
    """The native codec includes TOOL_DEFINITIONS in every InferenceRequest."""
    captured = []

    def mock_dispatch(req):
        captured.append(req)
        return _make_mock_response("DONE: ok")

    loop, _ = _loop_with(mock_dispatch)
    _run(loop, "T-t")
    assert captured[0].tools == TOOL_DEFINITIONS


def test_loop_correction_injected_on_turn1_planning():
    """Turn 1 with no tool_calls and no terminal envelope → correction injected, loop continues."""
    responses = [
        _make_mock_response("Let me analyze the ticket first."),  # turn 1: planning, no tools
        _make_mock_response("DONE: fixed it"),                    # turn 2: done after correction
    ]
    dispatch_calls = []

    def mock_dispatch(req):
        dispatch_calls.append(req)
        return responses.pop(0)

    loop, _ = _loop_with(mock_dispatch)
    result = _run(loop, "T-corr")

    assert len(dispatch_calls) == 2
    second_messages = dispatch_calls[1].messages
    user_messages = [m for m in second_messages if m["role"] == "user"]
    assert len(user_messages) == 2  # original + correction
    assert "call a tool" in user_messages[1]["content"]
    assert result.text == "DONE: fixed it"


def test_loop_no_correction_when_done_on_turn1():
    """Turn 1 with a DONE envelope and no tool_calls → done immediately, no correction."""
    dispatch_calls = []

    def mock_dispatch(req):
        dispatch_calls.append(req)
        return _make_mock_response("DONE: nothing needed")

    loop, _ = _loop_with(mock_dispatch)
    result = _run(loop, "T-done1")

    assert len(dispatch_calls) == 1
    assert result.text == "DONE: nothing needed"


def test_loop_inference_failure_returns_availability():
    """Inference raises — the loop returns LOOP_AVAILABILITY (source-down, not capability)."""
    loop, _ = _loop_with(RuntimeError("no model"))
    result = _run(loop, "T-4")
    assert result.outcome == LOOP_AVAILABILITY


def test_loop_max_turns_respected():
    """Loop stops at max_turns even with continuous tool calls."""
    tc = _bash_call("echo still going", "call_loop")
    call_count = [0]

    def always_tool(req):
        call_count[0] += 1
        return _make_mock_response("", tool_calls=[tc])

    loop, _ = _loop_with(always_tool, max_turns=3)
    result = _run(loop, "T-5")

    assert call_count[0] == 3
    assert result.outcome == LOOP_MAX_TURNS
    assert "MAX_TURNS" in result.text


def test_loop_max_turns_sentinel_content():
    """MAX_TURNS text includes the turn count for diagnostics."""
    tc = _bash_call("echo loop", "call_sentinel")

    def always_tool(req):
        return _make_mock_response("", tool_calls=[tc])

    loop, _ = _loop_with(always_tool, max_turns=2)
    result = _run(loop, "T-s")
    assert "2" in result.text, "turn count must appear in sentinel for diagnostics"


# ── DickSimnelDevice._run_inference delegates to the coding domain ─────────────


def test_run_inference_delegates_to_domain():
    from unseen_university.devices.dicksimnel.device import DickSimnelDevice

    d = DickSimnelDevice.__new__(DickSimnelDevice)
    d._active_ticket = None
    ticket = {"id": "T-tl", "title": "T", "tags": [], "description": "d"}
    with patch("unseen_university.capabilities.base.CapabilityMixin.run_capability") as spy:
        spy.return_value = "DONE: fixed"
        result = d._run_inference(ticket)

    spy.assert_called_once_with(ticket, agent_id="DS.0")
    assert result == "DONE: fixed"


def test_loop_turn_log_populated():
    """_turn_log is populated after run() and cleared on re-run."""
    bash_call = _bash_call("echo hi", "call_1")
    responses = [
        _make_mock_response("thinking...", [bash_call]),
        _make_mock_response("DONE: done"),
    ]
    loop, _ = _loop_with(responses, max_turns=5)
    _run(loop, "T-log")

    assert len(loop._turn_log) == 2
    assert loop._turn_log[0]["had_tool_calls"] is True
    assert "Bash" in loop._turn_log[0]["tool_names"]
    assert loop._turn_log[1]["had_tool_calls"] is False


def test_loop_turn_log_cleared_on_rerun():
    """_turn_log is cleared at the start of each run() — no stale entries."""
    loop, _ = _loop_with([_make_mock_response("DONE: quick")], max_turns=5)
    loop._turn_log = [{"turn": 99, "had_tool_calls": True, "tool_names": ["Bash"]}]
    _run(loop, "T-clear")

    assert len(loop._turn_log) == 1
    assert loop._turn_log[0]["turn"] == 1


# ── Integration smoke test (DICKSIMNEL_LIVE_OR=1 required) ───────────────────


@pytest.mark.integration
class TestAgenticLoopLiveOR:
    """Real OR call smoke test — skipped unless DICKSIMNEL_LIVE_OR=1."""

    _SKIP = pytest.mark.skipif(
        __import__("os").getenv("DICKSIMNEL_LIVE_OR") != "1",
        reason="DICKSIMNEL_LIVE_OR=1 required for live OR integration test",
    )

    @_SKIP
    def test_echo_ticket_returns_done_with_tool_calls(self):
        """AgenticLoop completes a minimal echo ticket against real OR."""
        loop = AgenticLoop(codec=NativeToolCodec(), max_turns=10)
        result = loop.run(
            system_prompt="You are a minimal test worker.",
            initial_message="Run: echo hello via the Bash tool and return DONE: hello echoed",
            ticket_id="T-smoke-test",
        )
        assert result.outcome == LOOP_DONE, f"expected DONE, got {result.outcome}: {result.text[:80]!r}"
        tool_call_turns = [t for t in loop._turn_log if t["had_tool_calls"]]
        assert tool_call_turns, f"No tool_calls seen in any turn. turn_log: {loop._turn_log}"


# ── bash-failure escalation hint (preserved from minion, generalized to the shared loop) ──


def test_bash_failure_hint_after_three_consecutive_failures():
    """After 3 consecutive failed Bash calls the loop appends an escalation hint to the result."""
    responses = [_make_mock_response("", tool_calls=[_bash_call("false")]) for _ in range(3)]
    responses.append(_make_mock_response('{"status": "done", "result": "ok"}'))
    it = iter(responses)
    seen_tool_content: list[str] = []

    def dispatch(req):
        seen_tool_content.extend(
            m.get("content", "") for m in req.messages if m.get("role") == "tool"
        )
        return next(it)

    device = MagicMock()
    device.dispatch.side_effect = dispatch
    loop = AgenticLoop(codec=NativeToolCodec(), max_turns=50, inference_device=device)
    with patch(
        "unseen_university.devices.inference.agentic_loop.execute_tool",
        return_value="[Bash rc=1]\nboom",
    ):
        result = loop.run(system_prompt="s", initial_message="go", ticket_id="T")
    assert result.outcome == LOOP_DONE
    # The 3rd consecutive failure's tool result (seen on the 4th dispatch) carries the hint.
    assert any("[HINT:" in c for c in seen_tool_content), "expected escalation hint after 3 bash fails"


def test_bash_success_resets_failure_counter():
    """A successful Bash call resets the counter — no hint until 3 *consecutive* failures."""
    # fail, fail, success, fail → never 3 consecutive → no hint.
    responses = [_make_mock_response("", tool_calls=[_bash_call("cmd")]) for _ in range(4)]
    responses.append(_make_mock_response('{"status": "done", "result": "ok"}'))
    it = iter(responses)
    seen_tool_content: list[str] = []
    exec_results = iter(["[Bash rc=1]\nx", "[Bash rc=1]\nx", "[Bash rc=0]\nok", "[Bash rc=1]\nx"])

    def dispatch(req):
        seen_tool_content.extend(m.get("content", "") for m in req.messages if m.get("role") == "tool")
        return next(it)

    device = MagicMock()
    device.dispatch.side_effect = dispatch
    loop = AgenticLoop(codec=NativeToolCodec(), max_turns=50, inference_device=device)
    with patch("unseen_university.devices.inference.agentic_loop.execute_tool", side_effect=lambda *a, **k: next(exec_results)):
        result = loop.run(system_prompt="s", initial_message="go", ticket_id="T")
    assert result.outcome == LOOP_DONE
    assert not any("[HINT:" in c for c in seen_tool_content), "no hint without 3 CONSECUTIVE fails"
