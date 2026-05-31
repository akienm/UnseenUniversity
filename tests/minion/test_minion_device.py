"""
Tests for the minion worker device: shim, tool_loop, device.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devices.minion.shim import MinionShim, WorkerEnvelope, WorkerResult
from devices.minion.tool_loop import (
    ToolLoop,
    _execute_tool,
    _parse_signal,
    _parse_tool_call,
)
from devices.minion.device import MinionDevice
from devices.inference.shim import InferenceResponse

# ── MinionShim ────────────────────────────────────────────────────────────────


def test_shim_start_stop():
    shim = MinionShim()
    assert shim.start() is True
    assert shim.stop() is True


def test_shim_self_test():
    result = MinionShim().self_test()
    assert result["passed"] is True


def test_shim_device_id():
    assert MinionShim().device_id == "minion"


# ── WorkerEnvelope / WorkerResult ─────────────────────────────────────────────


def test_worker_envelope_defaults():
    env = WorkerEnvelope(ticket_id="T-foo", description="do the thing")
    assert env.repo_map == ""
    assert env.session_id == ""
    assert env.escalation_history == []
    assert env.cwd == ""


def test_worker_result_signal_field():
    r = WorkerResult(signal="DONE", notes="all good")
    assert r.signal == "DONE"
    assert r.iterations == 0
    assert r.tools_called == []


# ── _parse_tool_call ──────────────────────────────────────────────────────────


def test_parse_tool_call_read():
    text = (
        "Let me read the file.\n<tool>Read</tool><path>devices/granny/device.py</path>"
    )
    action = _parse_tool_call(text)
    assert action == {"tool": "Read", "path": "devices/granny/device.py"}


def test_parse_tool_call_bash():
    text = "<tool>Bash</tool><command>pytest tests/ -q --tb=short</command>"
    action = _parse_tool_call(text)
    assert action == {"tool": "Bash", "command": "pytest tests/ -q --tb=short"}


def test_parse_tool_call_edit():
    text = textwrap.dedent("""\
        <tool>Edit</tool><path>foo.py</path>
        <old_string>def old():\n    pass</old_string>
        <new_string>def old():\n    return 1</new_string>
    """)
    action = _parse_tool_call(text)
    assert action is not None
    assert action["tool"] == "Edit"
    assert action["path"] == "foo.py"
    assert "def old():" in action["old_string"]


def test_parse_tool_call_write():
    text = "<tool>Write</tool><path>new.py</path><content>x = 1\n</content>"
    action = _parse_tool_call(text)
    assert action == {"tool": "Write", "path": "new.py", "content": "x = 1\n"}


def test_parse_tool_call_returns_none_when_absent():
    assert _parse_tool_call("I am thinking about what to do next.") is None


def test_parse_tool_call_case_insensitive():
    text = "<tool>read</tool><path>some/file.py</path>"
    action = _parse_tool_call(text)
    assert action is not None
    assert action["tool"] == "Read"


# ── _parse_signal ─────────────────────────────────────────────────────────────


def test_parse_signal_done():
    sig = _parse_signal("Some reasoning...\nDONE: added retry logic to broker.py")
    assert sig == ("DONE", "added retry logic to broker.py")


def test_parse_signal_escalate_worker():
    sig = _parse_signal("ESCALATE: worker\nTried 3 times, test still fails.")
    assert sig is not None
    assert sig[0] == "ESCALATE: worker"
    assert "Tried 3 times" in sig[1]


def test_parse_signal_escalate_analyst():
    sig = _parse_signal(
        "This requires design.\nESCALATE: analyst\nNeeds cross-file reasoning."
    )
    assert sig is not None
    assert sig[0] == "ESCALATE: analyst"


def test_parse_signal_escalate_designer():
    sig = _parse_signal("ESCALATE: designer\nTouches auth middleware.")
    assert sig is not None
    assert sig[0] == "ESCALATE: designer"


def test_parse_signal_returns_none_when_absent():
    assert _parse_signal("Still thinking, need to read more files.") is None


# ── _execute_tool (filesystem/bash) ──────────────────────────────────────────


def test_execute_tool_read(tmp_path):
    (tmp_path / "hello.txt").write_text("hello world")
    result = _execute_tool({"tool": "Read", "path": "hello.txt"}, tmp_path)
    assert "[Read hello.txt]" in result
    assert "hello world" in result


def test_execute_tool_read_missing(tmp_path):
    result = _execute_tool({"tool": "Read", "path": "missing.txt"}, tmp_path)
    assert "[Read ERROR]" in result


def test_execute_tool_bash(tmp_path):
    result = _execute_tool({"tool": "Bash", "command": "echo hi"}, tmp_path)
    assert "[Bash rc=0]" in result
    assert "hi" in result


def test_execute_tool_bash_nonzero(tmp_path):
    result = _execute_tool({"tool": "Bash", "command": "exit 1"}, tmp_path)
    assert "[Bash rc=1]" in result


def test_execute_tool_edit(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def foo():\n    pass\n")
    result = _execute_tool(
        {
            "tool": "Edit",
            "path": "code.py",
            "old_string": "    pass",
            "new_string": "    return 42",
        },
        tmp_path,
    )
    assert "[Edit OK]" in result
    assert "return 42" in f.read_text()


def test_execute_tool_edit_old_not_found(tmp_path):
    (tmp_path / "code.py").write_text("def foo(): pass\n")
    result = _execute_tool(
        {"tool": "Edit", "path": "code.py", "old_string": "NOPE", "new_string": "x"},
        tmp_path,
    )
    assert "[Edit ERROR]" in result


def test_execute_tool_write(tmp_path):
    result = _execute_tool(
        {"tool": "Write", "path": "new.py", "content": "x = 1\n"},
        tmp_path,
    )
    assert "[Write OK]" in result
    assert (tmp_path / "new.py").read_text() == "x = 1\n"


# ── ToolLoop (mock InferenceDevice) ──────────────────────────────────────────


def _mock_inference(responses: list[str]) -> MagicMock:
    """Build a mock InferenceDevice that returns responses in sequence."""
    inf = MagicMock()
    inf.dispatch.side_effect = [
        InferenceResponse(text=r, model="test/model") for r in responses
    ]
    return inf


def test_tool_loop_done_signal(tmp_path):
    inf = _mock_inference(["DONE: fixed the bug in broker.py"])
    loop = ToolLoop(inf, cwd=tmp_path)
    env = WorkerEnvelope(ticket_id="T-test", description="Fix the bug")
    result = loop.run(env)
    assert result.signal == "DONE"
    assert "fixed the bug" in result.notes
    assert result.iterations == 1


def test_tool_loop_escalate_signal(tmp_path):
    inf = _mock_inference(["ESCALATE: worker\nTried 3 times, test still fails."])
    loop = ToolLoop(inf, cwd=tmp_path)
    env = WorkerEnvelope(ticket_id="T-test", description="Fix flaky test")
    result = loop.run(env)
    assert result.signal == "ESCALATE: worker"
    assert result.iterations == 1


def test_tool_loop_executes_tool_then_done(tmp_path):
    (tmp_path / "code.py").write_text("x = 1\n")
    responses = [
        "<tool>Read</tool><path>code.py</path>",
        "DONE: read the file successfully",
    ]
    inf = _mock_inference(responses)
    loop = ToolLoop(inf, cwd=tmp_path)
    result = loop.run(WorkerEnvelope(ticket_id="T-t", description="read code.py"))
    assert result.signal == "DONE"
    assert result.iterations == 2
    assert "Read" in result.tools_called


def test_tool_loop_max_iterations(tmp_path):
    # Always returns a tool call, never DONE — should hit max
    inf = _mock_inference(["<tool>Bash</tool><command>echo hi</command>"] * 5)
    loop = ToolLoop(inf, cwd=tmp_path, max_iterations=3)
    result = loop.run(WorkerEnvelope(ticket_id="T-t", description="loop forever"))
    assert result.signal == "ESCALATE: worker"
    assert "max iterations" in result.notes
    assert result.iterations == 3


def test_tool_loop_inference_error_escalates(tmp_path):
    inf = MagicMock()
    inf.dispatch.side_effect = RuntimeError("API down")
    loop = ToolLoop(inf, cwd=tmp_path)
    result = loop.run(WorkerEnvelope(ticket_id="T-t", description="..."))
    assert result.signal == "ESCALATE: worker"
    assert "Inference error" in result.notes


# ── MinionDevice ──────────────────────────────────────────────────────────────


def test_minion_device_execute_done(tmp_path):
    inf = _mock_inference(["DONE: ticket complete"])
    device = MinionDevice(inference=inf)
    device._loop = ToolLoop(inf, cwd=tmp_path)
    result = device.execute(WorkerEnvelope(ticket_id="T-t", description="do it"))
    assert result.signal == "DONE"
    assert len(device.run_history()) == 1
    assert device.run_history()[0]["ticket_id"] == "T-t"


def test_minion_device_health_healthy():
    inf = MagicMock()
    inf.health.return_value = {"status": "healthy", "detail": "ok"}
    device = MinionDevice(inference=inf)
    h = device.health()
    assert h["status"] == "healthy"


def test_minion_device_health_degraded():
    inf = MagicMock()
    inf.health.return_value = {"status": "unhealthy", "detail": "OR unreachable"}
    device = MinionDevice(inference=inf)
    h = device.health()
    assert h["status"] == "degraded"
    assert "OR unreachable" in h["detail"]


def test_minion_device_who_am_i():
    inf = MagicMock()
    inf.health.return_value = {"status": "healthy"}
    inf.startup_errors.return_value = []
    device = MinionDevice(inference=inf)
    info = device.who_am_i()
    assert info["device_id"] == "minion"


def test_minion_device_restart_resets_loop():
    inf = MagicMock()
    inf.health.return_value = {"status": "healthy"}
    device = MinionDevice(inference=inf)
    old_loop = device._loop
    device.restart()
    assert device._loop is not old_loop
