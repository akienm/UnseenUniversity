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
    _parse_advisor_signal,
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
    assert env.task_class == "worker"


def test_worker_result_signal_field():
    r = WorkerResult(signal="DONE", notes="all good")
    assert r.signal == "DONE"
    assert r.iterations == 0
    assert r.tools_called == []
    assert r.input_tokens == 0
    assert r.output_tokens == 0
    assert r.cost_usd == 0.0


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


def test_tool_loop_uses_envelope_task_class(tmp_path):
    """ToolLoop must forward envelope.task_class to InferenceRequest (not hard-code 'worker')."""
    inf = MagicMock()
    inf.dispatch.return_value = InferenceResponse(text="DONE: done", model="test/model")
    loop = ToolLoop(inf, cwd=tmp_path)
    env = WorkerEnvelope(ticket_id="T-t", description="go", task_class="minion")
    loop.run(env)
    req = inf.dispatch.call_args.args[0]
    assert req.task_class == "minion"


def test_tool_loop_accumulates_cost(tmp_path):
    """Cost is summed from registry pricing across iterations; returned in WorkerResult."""
    from devices.inference.models_registry import default_registry

    model_id = "qwen/qwen3.5-9b"
    spec = default_registry().get(model_id)
    assert spec is not None, "qwen3.5-9b must exist in registry for this test"

    inf = MagicMock()
    # Two iterations: first a tool call, then DONE
    inf.dispatch.side_effect = [
        InferenceResponse(
            text="<tool>Bash</tool><command>echo hi</command>",
            model=model_id,
            input_tokens=1000,
            output_tokens=200,
        ),
        InferenceResponse(
            text="DONE: finished",
            model=model_id,
            input_tokens=500,
            output_tokens=50,
        ),
    ]
    loop = ToolLoop(inf, cwd=tmp_path)
    result = loop.run(
        WorkerEnvelope(ticket_id="T-t", description="go", task_class="minion")
    )

    expected_cost = spec.cost_estimate(1500, 250)
    assert result.input_tokens == 1500
    assert result.output_tokens == 250
    assert abs(result.cost_usd - expected_cost) < 1e-9


# ── _parse_advisor_signal ─────────────────────────────────────────────────────


def test_parse_advisor_signal_continue():
    assert _parse_advisor_signal("CONTINUE") == ("CONTINUE", "")


def test_parse_advisor_signal_reprompt():
    sig, notes = _parse_advisor_signal(
        "REPROMPT: Add the full file path to the description."
    )
    assert sig == "REPROMPT"
    assert "full file path" in notes


def test_parse_advisor_signal_upgrade():
    sig, _ = _parse_advisor_signal("UPGRADE\nRequires cross-file reasoning.")
    assert sig == "UPGRADE"


def test_parse_advisor_signal_blocked():
    sig, notes = _parse_advisor_signal(
        "BLOCKED: ECONNREFUSED — no DB access in this env."
    )
    assert sig == "BLOCKED"
    assert "ECONNREFUSED" in notes


def test_parse_advisor_signal_confused():
    sig, _ = _parse_advisor_signal("CONFUSED")
    assert sig == "CONFUSED"


def test_parse_advisor_signal_escalate():
    sig, _ = _parse_advisor_signal("ESCALATE\nScope is wrong.")
    assert sig == "ESCALATE"


def test_parse_advisor_signal_unknown_returns_confused():
    sig, notes = _parse_advisor_signal("I think you should try harder.")
    assert sig == "CONFUSED"
    assert "Unrecognised" in notes


# ── ToolLoop round-based mode ─────────────────────────────────────────────────


def _mock_responses(*texts):
    """Build a mock InferenceDevice with sequential responses."""
    inf = MagicMock()
    inf.dispatch.side_effect = [
        InferenceResponse(text=t, model="test/model") for t in texts
    ]
    return inf


def test_tool_loop_round_transition_advisor_called(tmp_path):
    """Round 1 exhausts → advisor called → CONTINUE → round 2 completes."""
    tool_call = "<tool>Bash</tool><command>echo hi</command>"
    # Round 1: 2 tool calls (no signal), then advisor: CONTINUE, Round 2: DONE
    inf = _mock_responses(tool_call, tool_call, "CONTINUE", "DONE: finished in round 2")
    loop = ToolLoop(inf, cwd=tmp_path, iterations_per_round=2, max_rounds=2)
    result = loop.run(WorkerEnvelope(ticket_id="T-t", description="do it"))
    assert result.signal == "DONE"
    assert result.round_count == 2
    assert result.advisor_calls == 1


def test_tool_loop_reprompt_carried_into_round2(tmp_path):
    """REPROMPT advisor signal → round 2 system prompt uses rewritten description."""
    tool_call = "<tool>Bash</tool><command>echo hi</command>"
    new_desc = "Updated: specify the exact file path: devices/foo.py"
    inf = _mock_responses(
        tool_call, tool_call, f"REPROMPT: {new_desc}", "DONE: done with new prompt"
    )
    loop = ToolLoop(inf, cwd=tmp_path, iterations_per_round=2, max_rounds=2)
    result = loop.run(WorkerEnvelope(ticket_id="T-t", description="vague description"))
    assert result.signal == "DONE"
    assert result.advisor_signal == "REPROMPT"
    # Round 2 inference request should contain the rewritten description
    round2_req = inf.dispatch.call_args_list[3].args[0]
    assert new_desc in round2_req.system


def test_tool_loop_upgrade_returns_analyst_escalate(tmp_path):
    """UPGRADE advisor signal → WorkerResult.signal == 'ESCALATE: analyst'."""
    tool_call = "<tool>Bash</tool><command>echo hi</command>"
    inf = _mock_responses(tool_call, tool_call, "UPGRADE\nNeeds cross-file reasoning.")
    loop = ToolLoop(inf, cwd=tmp_path, iterations_per_round=2, max_rounds=2)
    result = loop.run(WorkerEnvelope(ticket_id="T-t", description="design task"))
    assert result.signal == "ESCALATE: analyst"
    assert result.advisor_signal == "UPGRADE"


def test_tool_loop_blocked_short_circuits_round2(tmp_path):
    """BLOCKED advisor signal → ESCALATE: worker immediately, no round 2."""
    tool_call = "<tool>Bash</tool><command>echo hi</command>"
    inf = _mock_responses(tool_call, tool_call, "BLOCKED: ECONNREFUSED — no DB")
    loop = ToolLoop(inf, cwd=tmp_path, iterations_per_round=2, max_rounds=2)
    result = loop.run(WorkerEnvelope(ticket_id="T-t", description="db task"))
    assert result.signal == "ESCALATE: worker"
    assert "BLOCKED" in result.notes
    assert result.advisor_signal == "BLOCKED"
    assert inf.dispatch.call_count == 3  # 2 work + 1 advisor, no round 2


def test_tool_loop_confused_escalates(tmp_path):
    """CONFUSED advisor signal → ESCALATE: worker with CONFUSED in notes."""
    tool_call = "<tool>Bash</tool><command>echo hi</command>"
    inf = _mock_responses(tool_call, tool_call, "CONFUSED")
    loop = ToolLoop(inf, cwd=tmp_path, iterations_per_round=2, max_rounds=2)
    result = loop.run(WorkerEnvelope(ticket_id="T-t", description="mystery task"))
    assert result.signal == "ESCALATE: worker"
    assert "CONFUSED" in result.notes
    assert result.advisor_calls == 1


def test_tool_loop_context_reset_between_rounds(tmp_path):
    """Round 2's first inference call should NOT contain round 1's full message history."""
    tool_call = "<tool>Bash</tool><command>echo hi</command>"
    inf = _mock_responses(tool_call, tool_call, "CONTINUE", "DONE: done")
    loop = ToolLoop(inf, cwd=tmp_path, iterations_per_round=2, max_rounds=2)
    loop.run(WorkerEnvelope(ticket_id="T-t", description="go"))
    # Round 2 first call (index 3) should have a fresh messages list (just one user msg)
    round2_req = inf.dispatch.call_args_list[3].args[0]
    assert len(round2_req.messages) == 1


def test_tool_loop_result_includes_round_count_and_advisor_fields(tmp_path):
    """WorkerResult carries round_count, advisor_calls, advisor_signal."""
    inf = _mock_responses("DONE: quick win")
    loop = ToolLoop(inf, cwd=tmp_path, iterations_per_round=5, max_rounds=2)
    result = loop.run(WorkerEnvelope(ticket_id="T-t", description="easy"))
    assert result.round_count == 1
    assert result.advisor_calls == 0
    assert result.advisor_signal is None


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
