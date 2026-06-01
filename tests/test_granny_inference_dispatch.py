"""
Tests for inference_dispatch_fn — MinionDevice integration in Granny dispatch.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from devices.minion.shim import WorkerResult


@pytest.fixture(autouse=True)
def _no_channel(monkeypatch):
    """Prevent tests from posting to the real shared channel."""
    monkeypatch.setattr(
        "unseen_university.channel.post_to_channel", lambda *a, **kw: None
    )


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


@patch("devices.granny.dispatch.subprocess.run")
@patch("devices.minion.device.MinionDevice")
def test_inference_dispatch_escalate_holds_ticket(MockDevice, mock_run):
    from devices.granny.dispatch import inference_dispatch_fn

    mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
    instance = MockDevice.return_value
    instance.execute.return_value = WorkerResult(
        signal="ESCALATE: worker", notes="test keeps failing after 3 tries"
    )

    result = inference_dispatch_fn(_ticket())

    assert result is True
    cmds = [c.args[0] for c in mock_run.call_args_list if c.args]

    # ticket must be blocked (held) — never set-worker or setstatus sprint
    block_cmds = [c for c in cmds if "block" in c]
    assert block_cmds, "ticket must be blocked on ESCALATE"
    assert "T-test" in block_cmds[0]

    # must never launch CC
    set_worker = [c for c in cmds if "set-worker" in c]
    assert not set_worker, "set-worker must NOT be called — no CC spawn"
    setstatus_sprint = [c for c in cmds if "setstatus" in c and "sprint" in c]
    assert not setstatus_sprint, "setstatus sprint must NOT be called"


@patch("devices.granny.dispatch.subprocess.run")
@patch("devices.minion.device.MinionDevice")
def test_inference_dispatch_escalate_block_includes_reason(MockDevice, mock_run):
    from devices.granny.dispatch import inference_dispatch_fn

    mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
    instance = MockDevice.return_value
    instance.execute.return_value = WorkerResult(
        signal="ESCALATE: analyst", notes="needs design decision about auth middleware"
    )

    inference_dispatch_fn(_ticket())

    cmds = [c.args[0] for c in mock_run.call_args_list if c.args]
    block_cmds = [c for c in cmds if "block" in c]
    assert block_cmds, "block must be called on ESCALATE"
    block_reason = block_cmds[0][-1]
    assert "analyst" in block_reason
    assert "auth middleware" in block_reason


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
    # minion tag → task_class="minion" so rules engine picks qwen, not deepseek
    assert envelope.task_class == "minion"


@patch("devices.granny.dispatch.subprocess.run")
@patch("devices.minion.device.MinionDevice")
def test_inference_dispatch_non_minion_tag_uses_worker_envelope(MockDevice, mock_run):
    from devices.granny.dispatch import inference_dispatch_fn

    mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
    instance = MockDevice.return_value
    instance.execute.return_value = WorkerResult(signal="DONE", notes="done")

    inference_dispatch_fn(_ticket(tags=["Platform"]))

    envelope = instance.execute.call_args.args[0]
    assert envelope.task_class == "worker"


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


@patch("devices.granny.dispatch.subprocess.run")
@patch("devices.minion.device.MinionDevice")
def test_inference_dispatch_cost_in_channel_post(MockDevice, mock_run):
    """Cost fields from WorkerResult must appear in the MINION_RESULT channel post."""
    from unittest.mock import patch as upatch

    from devices.granny.dispatch import inference_dispatch_fn

    mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
    instance = MockDevice.return_value
    instance.execute.return_value = WorkerResult(
        signal="DONE",
        notes="done",
        cost_usd=0.0023,
        input_tokens=1500,
        output_tokens=300,
    )

    posted_msgs = []
    with upatch(
        "unseen_university.channel.post_to_channel",
        side_effect=lambda msg, **kw: posted_msgs.append(msg),
    ):
        inference_dispatch_fn(_ticket(tags=["minion"]))

    result_posts = [m for m in posted_msgs if "MINION_RESULT" in m]
    assert result_posts, "expected a MINION_RESULT channel post"
    post = result_posts[0]
    assert "cost_usd=" in post
    assert "tokens_in=1500" in post
    assert "tokens_out=300" in post


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
