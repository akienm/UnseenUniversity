"""
Unit tests for WorkspaceDevice — sandboxed read/write/bash tools.

WorkspaceDevice is fully in-process and safe to test without any mocking.
tmp_path provides an isolated workspace_root for each test.
"""

from __future__ import annotations

import os
import time

import pytest

from devices.workspace.device import WorkspaceDevice
from unseen_university.device import INTERFACE_VERSION


@pytest.fixture
def ws(tmp_path):
    return WorkspaceDevice(workspace_root=tmp_path)


# ── BaseDevice contract ───────────────────────────────────────────────────────


def test_who_am_i_required_keys(ws):
    info = ws.who_am_i()
    assert info["device_id"] == "workspace"
    assert "name" in info
    assert "version" in info


def test_requirements_has_deps(ws):
    reqs = ws.requirements()
    assert "deps" in reqs


def test_capabilities_has_required_keys(ws):
    caps = ws.capabilities()
    for key in ("can_send", "can_receive", "emitted_keywords"):
        assert key in caps


def test_capabilities_lists_mcp_tools(ws):
    caps = ws.capabilities()
    tools = caps.get("mcp_tools", [])
    for t in ("workspace_read_file", "workspace_write_file", "workspace_run_bash"):
        assert t in tools


def test_comms_has_required_keys(ws):
    c = ws.comms()
    for key in ("address", "mode", "supports_push", "supports_pull", "supports_nudge"):
        assert key in c


def test_comms_address_starts_with_comms(ws):
    assert ws.comms()["address"].startswith("comms://")


def test_interface_version(ws):
    assert ws.interface_version() == INTERFACE_VERSION


def test_health_healthy_when_root_exists(ws):
    h = ws.health()
    assert h["status"] == "healthy"


def test_health_degraded_when_root_missing():
    dev = WorkspaceDevice(workspace_root="/nonexistent/path/xyz")
    h = dev.health()
    assert h["status"] == "degraded"


def test_health_has_all_keys(ws):
    h = ws.health()
    for key in ("status", "detail", "checked_at"):
        assert key in h


def test_uptime_positive(ws):
    time.sleep(0.01)
    assert ws.uptime() > 0


def test_startup_errors_is_empty_list(ws):
    assert ws.startup_errors() == []


def test_logs_has_paths_key(ws):
    assert "paths" in ws.logs()


def test_update_info_has_required_keys(ws):
    info = ws.update_info()
    assert "current_version" in info
    assert "update_available" in info


def test_where_and_how_has_required_keys(ws):
    w = ws.where_and_how()
    for key in ("host", "pid", "launch_command"):
        assert key in w


def test_where_and_how_pid_is_current_process(ws):
    assert ws.where_and_how()["pid"] == os.getpid()


def test_restart_clears_errors(ws):
    ws.block("test")
    assert ws.startup_errors()
    ws.restart()
    assert ws.startup_errors() == []


def test_block_adds_to_errors(ws):
    ws.block("test block")
    assert any("blocked" in e for e in ws.startup_errors())


def test_halt_adds_to_errors(ws):
    ws.halt()
    errors = ws.startup_errors()
    assert errors


def test_recovery_clears_errors(ws):
    ws.block("reason")
    ws.recovery()
    assert ws.startup_errors() == []


# ── workspace_read_file ───────────────────────────────────────────────────────


def test_read_file_success(ws, tmp_path):
    (tmp_path / "hello.txt").write_text("hello world")
    result = ws.workspace_read_file("hello.txt")
    assert result["status"] == "ok"
    assert result["content"] == "hello world"


def test_read_file_missing(ws):
    result = ws.workspace_read_file("nonexistent.txt")
    assert result["status"] == "error"
    assert "not found" in result["message"]


def test_read_file_is_directory(ws, tmp_path):
    (tmp_path / "subdir").mkdir()
    result = ws.workspace_read_file("subdir")
    assert result["status"] == "error"


def test_read_file_path_escape_rejected(ws):
    result = ws.workspace_read_file("../../etc/passwd")
    assert result["status"] == "error"
    assert "escapes" in result["message"]


def test_read_file_absolute_path_escape_rejected(ws):
    result = ws.workspace_read_file("/etc/passwd")
    assert result["status"] == "error"


# ── workspace_write_file ──────────────────────────────────────────────────────


def test_write_file_success(ws, tmp_path):
    result = ws.workspace_write_file("output.txt", "written content")
    assert result["status"] == "ok"
    assert (tmp_path / "output.txt").read_text() == "written content"


def test_write_file_creates_parent_dirs(ws, tmp_path):
    result = ws.workspace_write_file("nested/dir/file.txt", "data")
    assert result["status"] == "ok"
    assert (tmp_path / "nested/dir/file.txt").exists()


def test_write_file_path_escape_rejected(ws):
    result = ws.workspace_write_file("../../tmp/evil.txt", "evil")
    assert result["status"] == "error"
    assert "escapes" in result["message"]


def test_write_file_overwrites_existing(ws, tmp_path):
    (tmp_path / "existing.txt").write_text("old")
    ws.workspace_write_file("existing.txt", "new")
    assert (tmp_path / "existing.txt").read_text() == "new"


# ── workspace_run_bash ────────────────────────────────────────────────────────


def test_run_bash_success(ws):
    result = ws.workspace_run_bash("echo hello")
    assert result["status"] == "ok"
    assert result["returncode"] == 0
    assert "hello" in result["stdout"]


def test_run_bash_nonzero_returncode(ws):
    result = ws.workspace_run_bash("exit 2")
    assert result["status"] == "ok"
    assert result["returncode"] == 2


def test_run_bash_stderr_captured(ws):
    result = ws.workspace_run_bash("echo error >&2")
    assert result["status"] == "ok"
    assert "error" in result["stderr"]


def test_run_bash_runs_in_workspace_root(ws, tmp_path):
    result = ws.workspace_run_bash("pwd")
    assert result["status"] == "ok"
    assert str(tmp_path) in result["stdout"]


def test_run_bash_timeout(ws):
    result = ws.workspace_run_bash("sleep 10", timeout_sec=0.1)
    assert result["status"] == "error"
    assert "timed out" in result["message"]


def test_run_bash_creates_file_in_workspace(ws, tmp_path):
    ws.workspace_run_bash("echo test > testfile.txt")
    assert (tmp_path / "testfile.txt").exists()
