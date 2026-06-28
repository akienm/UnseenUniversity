"""Tests for Granny workflow executor state machine."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.granny.workflow_executor import (
    WorkflowExecutor,
    _DONE_STATUSES,
    _FAILED_STATUSES,
    get_ticket_status,
    list_active_workflows,
    load_workflow_script,
    save_state,
    start_workflow,
    tick_workflow,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_workflows_dir(tmp_path, monkeypatch):
    """Redirect workflow state files to a temp directory."""
    wdir = tmp_path / "workflows"
    wdir.mkdir()
    monkeypatch.setattr("unseen_university.devices.granny.workflow_executor._WORKFLOWS_DIR", wdir)
    return wdir


@pytest.fixture()
def two_step_state():
    """A minimal two-step workflow state (step-2 depends on step-1)."""
    return {
        "workflow_id": "test-wf",
        "script_path": "/dev/null",
        "status": "running",
        "steps": {
            "step-1": {
                "status": "pending",
                "ticket": "T-foo",
                "dispatch": "DickSimnel.0",
                "after": [],
            },
            "step-2": {
                "status": "pending",
                "ticket": "T-bar",
                "dispatch": "DickSimnel.0",
                "after": ["step-1"],
            },
        },
        "started_at": "2026-06-07T00:00:00Z",
        "updated_at": "2026-06-07T00:00:00Z",
    }


# ── load_workflow_script ──────────────────────────────────────────────────────


def test_load_workflow_script_returns_id_and_steps(tmp_path):
    script = tmp_path / "mywf.py"
    script.write_text(
        'WORKFLOW_ID = "my-wf"\n'
        'STEPS = [{"id": "s1", "dispatch": "DickSimnel.0", "ticket": "T-x"}]\n'
    )
    result = load_workflow_script(script)
    assert result["workflow_id"] == "my-wf"
    assert result["steps"][0]["id"] == "s1"


def test_load_workflow_script_missing_file():
    with pytest.raises(Exception):
        load_workflow_script("/nonexistent/path.py")


# ── start_workflow ────────────────────────────────────────────────────────────


def test_start_workflow_creates_state_file(tmp_path, tmp_workflows_dir):
    script = tmp_path / "wf.py"
    script.write_text(
        'WORKFLOW_ID = "chain"\n'
        'STEPS = [\n'
        '  {"id": "s1", "dispatch": "DickSimnel.0", "ticket": "T-a", "after": []},\n'
        '  {"id": "s2", "dispatch": "DickSimnel.0", "ticket": "T-b", "after": ["s1"]},\n'
        ']\n'
    )
    state = start_workflow(script)
    assert state["workflow_id"] == "chain"
    assert state["status"] == "pending"
    assert (tmp_workflows_dir / "chain.json").exists()
    assert state["steps"]["s1"]["status"] == "pending"
    assert state["steps"]["s2"]["after"] == ["s1"]


# ── tick_workflow ─────────────────────────────────────────────────────────────


def test_tick_dispatches_step_with_no_deps(two_step_state):
    workers_cfg = {"DickSimnel.0": {"worker_name": "dicksimnel"}}
    with patch("unseen_university.devices.granny.workflow_executor._dispatch_step", return_value=True) as mock_disp:
        updated = tick_workflow(two_step_state, workers_cfg)
    # step-1 has no deps → dispatched
    assert updated["steps"]["step-1"]["status"] == "dispatched"
    # step-2 has dep on step-1 (not done yet) → still pending
    assert updated["steps"]["step-2"]["status"] == "pending"
    mock_disp.assert_called_once_with("step-1", "T-foo", "DickSimnel.0", workers_cfg)


def test_tick_gates_dependent_step(two_step_state):
    two_step_state["steps"]["step-1"]["status"] = "done"
    workers_cfg = {"DickSimnel.0": {"worker_name": "dicksimnel"}}
    with patch("unseen_university.devices.granny.workflow_executor._dispatch_step", return_value=True):
        updated = tick_workflow(two_step_state, workers_cfg)
    # step-1 done → step-2 gate passes → dispatched
    assert updated["steps"]["step-2"]["status"] == "dispatched"


def test_tick_does_not_dispatch_while_deps_pending(two_step_state):
    # step-1 is dispatched (not done) → step-2 still gated
    two_step_state["steps"]["step-1"]["status"] = "dispatched"
    workers_cfg = {}
    with patch("unseen_university.devices.granny.workflow_executor._dispatch_step") as mock_disp:
        updated = tick_workflow(two_step_state, workers_cfg)
    assert updated["steps"]["step-2"]["status"] == "pending"
    mock_disp.assert_not_called()


def test_tick_marks_completed_when_all_done(two_step_state):
    two_step_state["steps"]["step-1"]["status"] = "done"
    two_step_state["steps"]["step-2"]["status"] = "done"
    updated = tick_workflow(two_step_state, {})
    assert updated["status"] == "completed"


def test_tick_marks_failed_on_escalated_ticket(two_step_state):
    two_step_state["steps"]["step-1"]["status"] = "dispatched"
    with patch("unseen_university.devices.granny.workflow_executor.get_ticket_status", return_value="escalated"):
        updated = tick_workflow(two_step_state, {})
    assert updated["steps"]["step-1"]["status"] == "failed"
    assert updated["status"] == "failed"


def test_tick_advances_dispatched_to_done_on_closed(two_step_state):
    two_step_state["steps"]["step-1"]["status"] = "dispatched"
    with patch("unseen_university.devices.granny.workflow_executor.get_ticket_status", return_value="closed"), \
         patch("unseen_university.devices.granny.workflow_executor._dispatch_step", return_value=True):
        updated = tick_workflow(two_step_state, {})
    assert updated["steps"]["step-1"]["status"] == "done"


def test_tick_pending_to_running_on_first_call(two_step_state):
    two_step_state["status"] = "pending"
    workers_cfg = {}
    with patch("unseen_university.devices.granny.workflow_executor._dispatch_step", return_value=True):
        updated = tick_workflow(two_step_state, workers_cfg)
    assert updated["status"] == "running"


def test_tick_retries_dispatch_on_failure(two_step_state):
    """If dispatch fails, step stays pending and retries next cycle."""
    workers_cfg = {}
    with patch("unseen_university.devices.granny.workflow_executor._dispatch_step", return_value=False):
        updated = tick_workflow(two_step_state, workers_cfg)
    # step-1 has no deps but dispatch failed → stays pending
    assert updated["steps"]["step-1"]["status"] == "pending"


# ── list_active_workflows ─────────────────────────────────────────────────────


def test_list_active_excludes_completed(tmp_workflows_dir):
    for wid, status in [("wf-a", "running"), ("wf-b", "completed"), ("wf-c", "failed")]:
        (tmp_workflows_dir / f"{wid}.json").write_text(json.dumps({"workflow_id": wid, "status": status, "steps": {}}))
    active = list_active_workflows()
    ids = {w["workflow_id"] for w in active}
    assert "wf-a" in ids
    assert "wf-b" not in ids
    assert "wf-c" not in ids


def test_list_active_empty_when_no_dir(tmp_workflows_dir, monkeypatch):
    import shutil
    shutil.rmtree(tmp_workflows_dir)
    assert list_active_workflows() == []


# ── WorkflowExecutor.tick ─────────────────────────────────────────────────────


def test_executor_tick_calls_tick_workflow_for_each_active(tmp_workflows_dir):
    for wid in ("wf-1", "wf-2"):
        (tmp_workflows_dir / f"{wid}.json").write_text(json.dumps({
            "workflow_id": wid,
            "status": "running",
            "steps": {"s": {"status": "done", "ticket": "T-x", "dispatch": "DickSimnel.0", "after": []}},
            "started_at": "2026-06-07T00:00:00Z",
            "updated_at": "2026-06-07T00:00:00Z",
        }))
    exe = WorkflowExecutor()
    count = exe.tick({})
    assert count == 2
    # Both should have been saved as completed
    for wid in ("wf-1", "wf-2"):
        state = json.loads((tmp_workflows_dir / f"{wid}.json").read_text())
        assert state["status"] == "completed"


def test_executor_tick_returns_zero_when_no_workflows(tmp_workflows_dir):
    exe = WorkflowExecutor()
    assert exe.tick({}) == 0
