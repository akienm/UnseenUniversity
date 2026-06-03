"""Tests for DickSimnel ToolLoop (T-dicksimnel-toolloop)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ── _parse_tool_calls ─────────────────────────────────────────────────────────


def test_parse_single_bash_call():
    from devices.dicksimnel.toolloop import _parse_tool_calls
    text = "Let me run tests.\n<TOOL:Bash>pytest tests/ -q</TOOL>\nDone."
    calls = _parse_tool_calls(text)
    assert calls == [("Bash", "pytest tests/ -q")]


def test_parse_multiple_tool_calls():
    from devices.dicksimnel.toolloop import _parse_tool_calls
    text = (
        "<TOOL:Read>/tmp/foo.py</TOOL>\n"
        "<TOOL:Bash>echo hello</TOOL>"
    )
    calls = _parse_tool_calls(text)
    assert len(calls) == 2
    assert calls[0] == ("Read", "/tmp/foo.py")
    assert calls[1] == ("Bash", "echo hello")


def test_parse_no_tool_calls_returns_empty():
    from devices.dicksimnel.toolloop import _parse_tool_calls
    assert _parse_tool_calls("DONE: all fixed") == []


def test_parse_multiline_bash():
    from devices.dicksimnel.toolloop import _parse_tool_calls
    text = "<TOOL:Bash>git add x.py\ngit commit -m 'fix'\n</TOOL>"
    calls = _parse_tool_calls(text)
    assert calls[0][0] == "Bash"
    assert "git add" in calls[0][1]


# ── _execute_tool ─────────────────────────────────────────────────────────────


def test_execute_bash_returns_output():
    from devices.dicksimnel.toolloop import _execute_tool
    result = _execute_tool("Bash", "echo toolloop_test")
    assert "toolloop_test" in result


def test_execute_bash_denylist_blocks_rm_rf():
    from devices.dicksimnel.toolloop import _execute_tool
    result = _execute_tool("Bash", "rm -rf /tmp/test")
    assert "ERROR" in result
    assert "denylist" in result


def test_execute_bash_denylist_blocks_force_push():
    from devices.dicksimnel.toolloop import _execute_tool
    result = _execute_tool("Bash", "git push --force origin main")
    assert "ERROR" in result


def test_execute_read_existing_file(tmp_path):
    from devices.dicksimnel.toolloop import _execute_tool
    f = tmp_path / "test.py"
    f.write_text("def hello(): pass")
    result = _execute_tool("Read", str(f))
    assert "def hello" in result


def test_execute_read_missing_file():
    from devices.dicksimnel.toolloop import _execute_tool
    result = _execute_tool("Read", "/nonexistent/path/file.py")
    assert "ERROR" in result
    assert "not found" in result


def test_execute_edit_replaces_text(tmp_path):
    from devices.dicksimnel.toolloop import _execute_tool
    f = tmp_path / "code.py"
    f.write_text("def old_name(): pass\n")
    params = json.dumps({"file_path": str(f), "old_string": "old_name", "new_string": "new_name"})
    result = _execute_tool("Edit", params)
    assert "OK" in result
    assert "new_name" in f.read_text()


def test_execute_edit_fails_on_ambiguous_match(tmp_path):
    from devices.dicksimnel.toolloop import _execute_tool
    f = tmp_path / "dup.py"
    f.write_text("foo\nfoo\n")
    params = json.dumps({"file_path": str(f), "old_string": "foo", "new_string": "bar"})
    result = _execute_tool("Edit", params)
    assert "ERROR" in result
    assert "2" in result


def test_execute_write_creates_file(tmp_path):
    from devices.dicksimnel.toolloop import _execute_tool
    new_file = tmp_path / "new.py"
    params = json.dumps({"file_path": str(new_file), "content": "x = 1\n"})
    result = _execute_tool("Write", params)
    assert "OK" in result
    assert new_file.read_text() == "x = 1\n"


def test_execute_unknown_tool_returns_error():
    from devices.dicksimnel.toolloop import _execute_tool
    result = _execute_tool("Teleport", "somewhere")
    assert "ERROR" in result
    assert "unknown tool" in result


# ── ToolLoop.run ─────────────────────────────────────────────────────────────


def _make_mock_response(text: str, tokens: int = 100) -> MagicMock:
    r = MagicMock()
    r.text = text
    r.output_tokens = tokens
    r.cost_estimate = 0.001
    return r


def test_toolloop_done_on_first_turn():
    """Model emits DONE: on the first turn — loop completes in one call."""
    from devices.dicksimnel.toolloop import ToolLoop
    responses = [_make_mock_response("DONE: fixed it")]
    with patch("devices.inference.device.InferenceDevice.dispatch", side_effect=responses):
        loop = ToolLoop()
        result = loop.run({"id": "T-1", "title": "Fix", "tags": [], "description": "x"}, "system")
    assert result is not None
    assert result.startswith("DONE:")


def test_toolloop_multi_turn_bash_then_done():
    """Bash call on turn 1 → result injected → DONE on turn 2."""
    from devices.dicksimnel.toolloop import ToolLoop
    responses = [
        _make_mock_response("<TOOL:Bash>echo hello</TOOL>"),
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
    # Second call should include the tool result in the messages
    second_messages = dispatch_calls[1].messages
    user_messages = [m for m in second_messages if m["role"] == "user"]
    assert any("TOOL_RESULT:Bash" in m["content"] for m in user_messages)
    assert result is not None
    assert "DONE" in result


def test_toolloop_no_tool_calls_returns_text():
    """Model returns plain text with no tools and no DONE — loop treats it as implicit done."""
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
    """Loop stops at max_turns even without DONE signal."""
    from devices.dicksimnel.toolloop import ToolLoop

    call_count = [0]
    def always_bash(req):
        call_count[0] += 1
        return _make_mock_response("<TOOL:Bash>echo still going</TOOL>")

    with patch("devices.inference.device.InferenceDevice.dispatch", side_effect=always_bash):
        loop = ToolLoop(max_turns=3)
        result = loop.run({"id": "T-5", "title": "T", "tags": [], "description": "d"}, "s")

    assert call_count[0] == 3
    assert result is not None  # returns last assistant message


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
