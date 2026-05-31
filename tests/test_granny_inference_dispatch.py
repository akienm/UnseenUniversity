"""
Tests for inference_dispatch_fn — MinionDevice integration in Granny dispatch.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from devices.minion.shim import WorkerResult


def _ticket(tid="T-test", tags=None, worker=""):
    return {
        "id": tid,
        "title": "Do the thing",
        "size": "S",
        "description": "Make it work.",
        "tags": tags or ["Platform"],
        "worker": worker,
    }


# ── DONE path ─────────────────────────────────────────────────────────────────


@patch("devices.granny.dispatch.subprocess.run")
@patch("devices.minion.device.MinionDevice")
def test_inference_dispatch_done_submits_validation(MockDevice, mock_run):
    from devices.granny.dispatch import inference_dispatch_fn

    mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
    instance = MockDevice.return_value
    instance.execute.return_value = WorkerResult(signal="DONE", notes="fixed broker.py")

    result = inference_dispatch_fn(_ticket())

    assert result is True
    # cc_queue.py done should be called with the ticket id
    done_calls = [
        c for c in mock_run.call_args_list if "done" in (c.args[0] if c.args else [])
    ]
    assert done_calls, "expected cc_queue.py done to be called"
    # Should NOT call set-worker or setstatus sprint
    set_worker_calls = [
        c
        for c in mock_run.call_args_list
        if "set-worker" in (c.args[0] if c.args else [])
    ]
    assert not set_worker_calls, "set-worker should not be called on DONE"


@patch("devices.granny.dispatch.subprocess.run")
@patch("devices.minion.device.MinionDevice")
def test_inference_dispatch_done_summary_includes_notes(MockDevice, mock_run):
    from devices.granny.dispatch import inference_dispatch_fn

    mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
    instance = MockDevice.return_value
    instance.execute.return_value = WorkerResult(
        signal="DONE", notes="refactored shim lifecycle"
    )

    inference_dispatch_fn(_ticket())

    # Find the 'done' subprocess call and check its summary arg
    for c in mock_run.call_args_list:
        args = c.args[0] if c.args else []
        if "done" in args:
            summary_arg = args[-1]
            assert "refactored shim lifecycle" in summary_arg
            break


# ── ESCALATE path ─────────────────────────────────────────────────────────────


@patch("devices.granny.dispatch._launch_cc_instance")
@patch("devices.granny.dispatch.subprocess.run")
@patch("devices.minion.device.MinionDevice")
def test_inference_dispatch_escalate_sets_worker_claude(
    MockDevice, mock_run, mock_launch
):
    from devices.granny.dispatch import inference_dispatch_fn

    mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
    instance = MockDevice.return_value
    instance.execute.return_value = WorkerResult(
        signal="ESCALATE: worker", notes="test keeps failing after 3 tries"
    )

    result = inference_dispatch_fn(_ticket())

    assert result is True
    # set-worker claude should be called
    cmds = [c.args[0] for c in mock_run.call_args_list if c.args]
    set_worker = [c for c in cmds if "set-worker" in c]
    assert set_worker, "set-worker must be called on ESCALATE"
    assert "claude" in set_worker[0]

    # setstatus sprint should be called
    setstatus = [c for c in cmds if "setstatus" in c]
    assert setstatus, "setstatus must be called on ESCALATE"
    assert "sprint" in setstatus[0]

    # CC instance should be launched
    mock_launch.assert_called_once()


@patch("devices.granny.dispatch._launch_cc_instance")
@patch("devices.granny.dispatch.subprocess.run")
@patch("devices.minion.device.MinionDevice")
def test_inference_dispatch_escalate_logs_reason(MockDevice, mock_run, mock_launch):
    from devices.granny.dispatch import inference_dispatch_fn

    mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
    instance = MockDevice.return_value
    instance.execute.return_value = WorkerResult(
        signal="ESCALATE: analyst", notes="needs design decision about auth middleware"
    )

    inference_dispatch_fn(_ticket())

    # log command should include escalation notes
    cmds = [c.args[0] for c in mock_run.call_args_list if c.args]
    log_cmds = [c for c in cmds if "log" in c]
    assert log_cmds, "escalation log entry must be written"
    log_arg = log_cmds[0][-1]
    assert "ESCALATED" in log_arg
    assert "analyst" in log_arg


# ── task_class routing ────────────────────────────────────────────────────────


@patch("devices.granny.dispatch.subprocess.run")
@patch("devices.minion.device.MinionDevice")
def test_inference_dispatch_minion_tag_uses_minion_envelope(MockDevice, mock_run):
    from devices.granny.dispatch import inference_dispatch_fn

    mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
    instance = MockDevice.return_value
    instance.execute.return_value = WorkerResult(signal="DONE", notes="done")

    inference_dispatch_fn(_ticket(tags=["minion"]))

    # WorkerEnvelope is constructed inside the fn — check execute was called
    instance.execute.assert_called_once()
    envelope = instance.execute.call_args.args[0]
    # session_id == ticket_id for model affinity
    assert envelope.session_id == "T-test"
    assert envelope.ticket_id == "T-test"


@patch("devices.granny.dispatch.subprocess.run")
@patch("devices.minion.device.MinionDevice")
def test_inference_dispatch_cwd_is_repo_root(MockDevice, mock_run):
    from devices.granny.dispatch import inference_dispatch_fn, _UU_ROOT

    mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
    instance = MockDevice.return_value
    instance.execute.return_value = WorkerResult(signal="DONE", notes="done")

    inference_dispatch_fn(_ticket())

    envelope = instance.execute.call_args.args[0]
    assert envelope.cwd == str(_UU_ROOT)


# ── guard rails ───────────────────────────────────────────────────────────────


def test_inference_dispatch_no_id_returns_false():
    from devices.granny.dispatch import inference_dispatch_fn

    assert inference_dispatch_fn({}) is False
    assert inference_dispatch_fn({"id": ""}) is False


@patch("devices.granny.dispatch.subprocess.run")
@patch("devices.minion.device.MinionDevice")
def test_inference_dispatch_exception_returns_false(MockDevice, mock_run):
    from devices.granny.dispatch import inference_dispatch_fn

    mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
    MockDevice.return_value.execute.side_effect = RuntimeError("inference exploded")

    result = inference_dispatch_fn(_ticket())

    assert result is False
