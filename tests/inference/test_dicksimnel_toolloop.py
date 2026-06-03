"""Tests for DickSimnel ToolLoop — native OR tool calling (T-dicksimnel-native-tool-use)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_mock_response(text: str, tool_calls=None, tokens: int = 100) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.tool_calls = tool_calls
    r.output_tokens = tokens
    r.cost_estimate = 0.001
    return r


def _bash_call(command: str, call_id: str = "call_abc") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "Bash", "arguments": json.dumps({"command": command})},
    }


# ── _parse_response extracts tool_calls ───────────────────────────────────────


def test_parse_response_extracts_tool_calls():
    from devices.inference.device import _parse_response

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
    from devices.inference.device import _parse_response

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


# ── _execute_tool ─────────────────────────────────────────────────────────────


def test_execute_bash_returns_output():
    from devices.dicksimnel.toolloop import _execute_tool
    result = _execute_tool("Bash", {"command": "echo toolloop_test"})
    assert "toolloop_test" in result


def test_execute_bash_denylist_blocks_rm_rf():
    from devices.dicksimnel.toolloop import _execute_tool
    result = _execute_tool("Bash", {"command": "rm -rf /tmp/test"})
    assert "ERROR" in result
    assert "denylist" in result


def test_execute_bash_denylist_blocks_force_push():
    from devices.dicksimnel.toolloop import _execute_tool
    result = _execute_tool("Bash", {"command": "git push --force origin main"})
    assert "ERROR" in result


def test_execute_read_existing_file(tmp_path):
    from devices.dicksimnel.toolloop import _execute_tool
    f = tmp_path / "test.py"
    f.write_text("def hello(): pass")
    result = _execute_tool("Read", {"path": str(f)})
    assert "def hello" in result


def test_execute_read_missing_file():
    from devices.dicksimnel.toolloop import _execute_tool
    result = _execute_tool("Read", {"path": "/nonexistent/path/file.py"})
    assert "ERROR" in result
    assert "not found" in result


def test_execute_edit_replaces_text(tmp_path):
    from devices.dicksimnel.toolloop import _execute_tool
    f = tmp_path / "code.py"
    f.write_text("def old_name(): pass\n")
    result = _execute_tool("Edit", {"file_path": str(f), "old_string": "old_name", "new_string": "new_name"})
    assert "OK" in result
    assert "new_name" in f.read_text()


def test_execute_edit_fails_on_ambiguous_match(tmp_path):
    from devices.dicksimnel.toolloop import _execute_tool
    f = tmp_path / "dup.py"
    f.write_text("foo\nfoo\n")
    result = _execute_tool("Edit", {"file_path": str(f), "old_string": "foo", "new_string": "bar"})
    assert "ERROR" in result
    assert "2" in result


def test_execute_write_creates_file(tmp_path):
    from devices.dicksimnel.toolloop import _execute_tool
    new_file = tmp_path / "new.py"
    result = _execute_tool("Write", {"file_path": str(new_file), "content": "x = 1\n"})
    assert "OK" in result
    assert new_file.read_text() == "x = 1\n"


def test_execute_unknown_tool_returns_error():
    from devices.dicksimnel.toolloop import _execute_tool
    result = _execute_tool("Teleport", {"destination": "somewhere"})
    assert "ERROR" in result
    assert "unknown tool" in result


# ── ToolLoop.run ──────────────────────────────────────────────────────────────


def test_toolloop_done_on_first_turn():
    """Model returns no tool_calls on turn 1 — loop completes immediately."""
    from devices.dicksimnel.toolloop import ToolLoop
    responses = [_make_mock_response("DONE: fixed it")]
    with patch("devices.inference.device.InferenceDevice.dispatch", side_effect=responses):
        loop = ToolLoop()
        result = loop.run({"id": "T-1", "title": "Fix", "tags": [], "description": "x"}, "system")
    assert result is not None
    assert "DONE" in result


def test_toolloop_multi_turn_bash_then_done():
    """Tool call on turn 1 → result injected as role:tool → done on turn 2."""
    from devices.dicksimnel.toolloop import ToolLoop
    tc = _bash_call("echo hello", "call_1")
    responses = [
        _make_mock_response("", tool_calls=[tc]),
        _make_mock_response("DONE: ran hello"),
    ]
    dispatch_calls = []

    def mock_dispatch(req):
        dispatch_calls.append(req)
        return responses.pop(0)

    with patch("devices.inference.device.InferenceDevice.dispatch", side_effect=mock_dispatch):
        loop = ToolLoop()
        result = loop.run({"id": "T-2", "title": "Greet", "tags": [], "description": "y"}, "sys")

    assert len(dispatch_calls) == 2
    second_messages = dispatch_calls[1].messages
    tool_messages = [m for m in second_messages if m.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0]["tool_call_id"] == "call_1"
    assert "hello" in tool_messages[0]["content"]
    assert result is not None
    assert "DONE" in result


def test_toolloop_tool_result_uses_role_tool():
    """Tool results land as role:tool messages, not role:user."""
    from devices.dicksimnel.toolloop import ToolLoop
    tc = _bash_call("echo ping", "call_ping")
    responses = [
        _make_mock_response("", tool_calls=[tc]),
        _make_mock_response("DONE: done"),
    ]
    dispatch_calls = []

    def mock_dispatch(req):
        dispatch_calls.append(req)
        return responses.pop(0)

    with patch("devices.inference.device.InferenceDevice.dispatch", side_effect=mock_dispatch):
        ToolLoop().run({"id": "T-r", "title": "T", "tags": [], "description": "d"}, "s")

    second_messages = dispatch_calls[1].messages
    roles = [m["role"] for m in second_messages]
    assert "tool" in roles
    assert "user" not in roles[2:]  # after the initial user turn, no more user messages


def test_toolloop_sends_tool_definitions_in_request():
    """ToolLoop includes TOOL_DEFINITIONS in every InferenceRequest."""
    from devices.dicksimnel.toolloop import ToolLoop, TOOL_DEFINITIONS
    responses = [_make_mock_response("DONE: ok")]
    captured = []

    def mock_dispatch(req):
        captured.append(req)
        return responses.pop(0)

    with patch("devices.inference.device.InferenceDevice.dispatch", side_effect=mock_dispatch):
        ToolLoop().run({"id": "T-t", "title": "T", "tags": [], "description": "d"}, "s")

    assert captured[0].tools == TOOL_DEFINITIONS


def test_toolloop_no_tool_calls_returns_text():
    """Plain text response with no tool_calls is treated as done."""
    from devices.dicksimnel.toolloop import ToolLoop
    responses = [_make_mock_response("I analyzed the ticket. No changes needed.")]
    with patch("devices.inference.device.InferenceDevice.dispatch", side_effect=responses):
        loop = ToolLoop()
        result = loop.run({"id": "T-3", "title": "T", "tags": [], "description": "d"}, "s")
    assert result is not None
    assert "analyzed" in result


def test_toolloop_inference_failure_returns_none():
    """Inference raises — ToolLoop returns None."""
    from devices.dicksimnel.toolloop import ToolLoop
    with patch("devices.inference.device.InferenceDevice.dispatch", side_effect=RuntimeError("no model")):
        loop = ToolLoop()
        result = loop.run({"id": "T-4", "title": "T", "tags": [], "description": "d"}, "s")
    assert result is None


def test_toolloop_max_turns_respected():
    """Loop stops at max_turns even with continuous tool calls."""
    from devices.dicksimnel.toolloop import ToolLoop
    tc = _bash_call("echo still going", "call_loop")
    call_count = [0]

    def always_tool(req):
        call_count[0] += 1
        return _make_mock_response("", tool_calls=[tc])

    with patch("devices.inference.device.InferenceDevice.dispatch", side_effect=always_tool):
        loop = ToolLoop(max_turns=3)
        result = loop.run({"id": "T-5", "title": "T", "tags": [], "description": "d"}, "s")

    assert call_count[0] == 3
    assert result is not None


# ── DickSimnelDevice._run_inference uses ToolLoop ────────────────────────────


def test_run_inference_uses_toolloop():
    from devices.dicksimnel.device import DickSimnelDevice
    d = DickSimnelDevice()
    d._shim = MagicMock()

    mock_loop = MagicMock()
    mock_loop.run.return_value = "DONE: fixed"

    with patch("devices.dicksimnel.toolloop.ToolLoop", return_value=mock_loop):
        result = d._run_inference({"id": "T-tl", "title": "T", "tags": [], "description": "d"})

    mock_loop.run.assert_called_once()
    assert result == "DONE: fixed"
