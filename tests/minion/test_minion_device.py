"""
Tests for the minion worker device: shim, tool_loop adapter, device.

Minion's loop converged onto the shared inference/agentic_loop.py (D-domain-object-
encapsulation): minion drives AgenticLoop with the XML TextToolCodec. The XML parse/exec
helpers moved to agentic_loop (_parse_text_tool_call / _parse_text_signal / execute_tool);
minion/tool_loop.py is now the WorkerEnvelope↔AgenticLoop adapter. These tests exercise the
codec parsers, the shared tool executor, and the adapter's LoopResult→WorkerResult mapping.
"""

from __future__ import annotations

import textwrap
from unittest.mock import MagicMock

from unseen_university.devices.inference.agentic_loop import (
    execute_tool,
    _parse_text_signal,
    _parse_text_tool_call,
)
from unseen_university.devices.inference.shim import InferenceResponse
from unseen_university.devices.minion.device import MinionDevice
from unseen_university.devices.minion.shim import MinionShim, WorkerEnvelope, WorkerResult
from unseen_university.devices.minion.tool_loop import ToolLoop

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


# ── _parse_text_tool_call (XML codec) ─────────────────────────────────────────


def test_parse_tool_call_read():
    text = "Let me read the file.\n<tool>Read</tool><path>devices/granny/device.py</path>"
    action = _parse_text_tool_call(text)
    assert action == {"tool": "Read", "path": "devices/granny/device.py"}


def test_parse_tool_call_bash():
    text = "<tool>Bash</tool><command>pytest tests/ -q --tb=short</command>"
    action = _parse_text_tool_call(text)
    assert action == {"tool": "Bash", "command": "pytest tests/ -q --tb=short"}


def test_parse_tool_call_edit():
    text = textwrap.dedent("""\
        <tool>Edit</tool><path>foo.py</path>
        <old_string>def old():\n    pass</old_string>
        <new_string>def old():\n    return 1</new_string>
    """)
    action = _parse_text_tool_call(text)
    assert action is not None
    assert action["tool"] == "Edit"
    assert action["path"] == "foo.py"
    assert "def old():" in action["old_string"]


def test_parse_tool_call_write():
    text = "<tool>Write</tool><path>new.py</path><content>x = 1\n</content>"
    action = _parse_text_tool_call(text)
    assert action == {"tool": "Write", "path": "new.py", "content": "x = 1\n"}


def test_parse_tool_call_returns_none_when_absent():
    assert _parse_text_tool_call("I am thinking about what to do next.") is None


def test_parse_tool_call_case_insensitive():
    text = "<tool>read</tool><path>some/file.py</path>"
    action = _parse_text_tool_call(text)
    assert action is not None
    assert action["tool"] == "Read"


# ── _parse_text_signal (XML codec) — now returns a terminal envelope dict ──────


def test_parse_signal_done():
    sig = _parse_text_signal("Some reasoning...\nDONE: added retry logic to broker.py")
    assert sig == {"status": "done", "result": "added retry logic to broker.py"}


def test_parse_signal_escalate_worker():
    sig = _parse_text_signal("ESCALATE: worker\nTried 3 times, test still fails.")
    assert sig is not None
    assert sig["status"] == "escalate"
    assert sig["target"] == "worker"
    assert "Tried 3 times" in sig["result"]


def test_parse_signal_escalate_analyst():
    sig = _parse_text_signal("This requires design.\nESCALATE: analyst\nNeeds cross-file reasoning.")
    assert sig is not None
    assert sig["target"] == "analyst"


def test_parse_signal_escalate_designer():
    sig = _parse_text_signal("ESCALATE: designer\nTouches auth middleware.")
    assert sig is not None
    assert sig["target"] == "designer"


def test_parse_signal_returns_none_when_absent():
    assert _parse_text_signal("Still thinking, need to read more files.") is None


# ── execute_tool (shared filesystem/bash executor) ────────────────────────────


def test_execute_tool_read(tmp_path):
    (tmp_path / "hello.txt").write_text("hello world")
    result = execute_tool("Read", {"path": "hello.txt"}, tmp_path)
    assert "hello world" in result


def test_execute_tool_read_missing(tmp_path):
    result = execute_tool("Read", {"path": "missing.txt"}, tmp_path)
    assert "ERROR" in result and "not found" in result


def test_execute_tool_bash(tmp_path):
    result = execute_tool("Bash", {"command": "echo hi"}, tmp_path)
    assert "[Bash rc=0]" in result
    assert "hi" in result


def test_execute_tool_bash_nonzero(tmp_path):
    result = execute_tool("Bash", {"command": "exit 1"}, tmp_path)
    assert "[Bash rc=1]" in result


def test_execute_tool_edit(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def foo():\n    pass\n")
    result = execute_tool(
        "Edit", {"path": "code.py", "old_string": "    pass", "new_string": "    return 42"}, tmp_path
    )
    assert "OK: edited" in result
    assert "return 42" in f.read_text()


def test_execute_tool_edit_old_not_found(tmp_path):
    (tmp_path / "code.py").write_text("def foo(): pass\n")
    result = execute_tool(
        "Edit", {"path": "code.py", "old_string": "NOPE", "new_string": "x"}, tmp_path
    )
    assert "ERROR" in result and "not found" in result


def test_execute_tool_write(tmp_path):
    result = execute_tool("Write", {"path": "new.py", "content": "x = 1\n"}, tmp_path)
    assert "OK: wrote" in result
    assert (tmp_path / "new.py").read_text() == "x = 1\n"


# ── ToolLoop adapter (mock InferenceDevice) ───────────────────────────────────


def _resp(text: str, **kw) -> InferenceResponse:
    """A live (available) InferenceResponse — source_kind must be non-'none' or the shared
    loop's availability guard would treat the default 'none' as a source-down."""
    kw.setdefault("source_kind", "cloud")
    return InferenceResponse(text=text, model=kw.pop("model", "test/model"), **kw)


def _mock_inference(responses: list[str]) -> MagicMock:
    """Build a mock InferenceDevice that returns responses in sequence."""
    inf = MagicMock()
    inf.dispatch.side_effect = [_resp(r) for r in responses]
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


def test_tool_loop_escalate_preserves_target(tmp_path):
    """The tier-targeted escalation (worker/analyst/designer) survives the LoopResult mapping."""
    inf = _mock_inference(["ESCALATE: analyst\nNeeds cross-file reasoning."])
    result = ToolLoop(inf, cwd=tmp_path).run(WorkerEnvelope(ticket_id="T-t", description="x"))
    assert result.signal == "ESCALATE: analyst"


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
    # Always returns a tool call, never DONE — should hit max_turns → escalate.
    inf = _mock_inference(["<tool>Bash</tool><command>echo hi</command>"] * 5)
    loop = ToolLoop(inf, cwd=tmp_path, max_iterations=3)
    result = loop.run(WorkerEnvelope(ticket_id="T-t", description="loop forever"))
    assert result.signal == "ESCALATE: worker"
    assert "MAX_TURNS" in result.notes
    assert result.iterations == 3


def test_tool_loop_inference_error_escalates(tmp_path):
    inf = MagicMock()
    inf.dispatch.side_effect = RuntimeError("API down")
    loop = ToolLoop(inf, cwd=tmp_path)
    result = loop.run(WorkerEnvelope(ticket_id="T-t", description="..."))
    # A dispatch raise is an AVAILABILITY failure → escalate to worker, carrying the error text.
    assert result.signal == "ESCALATE: worker"
    assert "API down" in result.notes


def test_tool_loop_uses_envelope_task_class(tmp_path):
    """ToolLoop must forward envelope.task_class to InferenceRequest (not hard-code 'worker')."""
    inf = MagicMock()
    inf.dispatch.return_value = _resp("DONE: done")
    loop = ToolLoop(inf, cwd=tmp_path)
    env = WorkerEnvelope(ticket_id="T-t", description="go", task_class="minion")
    loop.run(env)
    req = inf.dispatch.call_args.args[0]
    assert req.task_class == "minion"


def test_tool_loop_accumulates_cost_and_tokens(tmp_path):
    """Tokens + cost are summed across iterations and returned in WorkerResult.

    Cost now comes from the InferenceResponse.cost_estimate the router reports (the shared
    loop's crossing value), replacing minion's prior local registry-pricing recompute.
    """
    inf = MagicMock()
    inf.dispatch.side_effect = [
        _resp("<tool>Bash</tool><command>echo hi</command>",
              input_tokens=1000, output_tokens=200, cost_estimate=0.001),
        _resp("DONE: finished", input_tokens=500, output_tokens=50, cost_estimate=0.002),
    ]
    loop = ToolLoop(inf, cwd=tmp_path)
    result = loop.run(WorkerEnvelope(ticket_id="T-t", description="go", task_class="minion"))
    assert result.input_tokens == 1500
    assert result.output_tokens == 250
    assert abs(result.cost_usd - 0.003) < 1e-9


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


# ── _map_signal: LoopResult → WorkerResult signal (R2: escalate target propagation) ──


def test_map_signal_covers_all_outcomes():
    """_map_signal maps every LoopResult outcome, preserving the escalate tier target."""
    from unseen_university.devices.minion.tool_loop import _map_signal
    from unseen_university.devices.inference.agentic_loop import (
        LOOP_AVAILABILITY,
        LOOP_COST_EXCEEDED,
        LOOP_DONE,
        LOOP_ESCALATE,
        LOOP_MAX_TURNS,
        LoopResult,
    )
    # done → DONE + notes from the envelope result
    assert _map_signal(
        LoopResult(LOOP_DONE, text="x", envelope={"status": "done", "result": "built it"})
    ) == ("DONE", "built it")
    # escalate carries the tier target THROUGH the mapping (R2 — the whole point)
    assert _map_signal(
        LoopResult(LOOP_ESCALATE, envelope={"status": "escalate", "result": "r", "target": "analyst"})
    ) == ("ESCALATE: analyst", "r")
    assert _map_signal(
        LoopResult(LOOP_ESCALATE, envelope={"status": "escalate", "result": "r2", "target": "designer"})
    ) == ("ESCALATE: designer", "r2")
    # non-terminal outcomes have no target → default worker tier
    assert _map_signal(LoopResult(LOOP_MAX_TURNS, text="mt"))[0] == "ESCALATE: worker"
    assert _map_signal(LoopResult(LOOP_AVAILABILITY, text="down"))[0] == "ESCALATE: worker"
    assert _map_signal(LoopResult(LOOP_COST_EXCEEDED, text="$$"))[0] == "ESCALATE: worker"
