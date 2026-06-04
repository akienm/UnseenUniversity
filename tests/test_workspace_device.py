"""Tests for devices/workspace/device.py — WorkspaceDevice.

Completion criteria:
  A test agent can announce, receive workspace tools in manifest,
  call workspace_read_file on a known file, and get correct content back.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from devices.workspace.device import WorkspaceDevice
from devices.workspace.shim import WorkspaceShim

CANONICAL_PROFILES = Path(__file__).parent.parent / "config" / "profiles"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def device(tmp_workspace: Path) -> WorkspaceDevice:
    return WorkspaceDevice(workspace_root=str(tmp_workspace))


# ── Device contract ───────────────────────────────────────────────────────────


def test_who_am_i_returns_expected_keys(device: WorkspaceDevice) -> None:
    info = device.who_am_i()
    assert info["device_id"] == "workspace"
    assert "name" in info
    assert "version" in info


def test_capabilities_lists_three_tools(device: WorkspaceDevice) -> None:
    caps = device.capabilities()
    tools = caps["mcp_tools"]
    assert "workspace_read_file" in tools
    assert "workspace_write_file" in tools
    assert "workspace_run_bash" in tools


def test_shim_contract() -> None:
    shim = WorkspaceShim()
    assert shim.device_id == "workspace"
    assert shim.start() is True
    assert shim.stop() is True
    assert shim.restart() is True
    result = shim.self_test()
    assert result["passed"] is True


# ── workspace_read_file ───────────────────────────────────────────────────────


def test_read_file_returns_correct_content(
    device: WorkspaceDevice, tmp_workspace: Path
) -> None:
    """Completion criterion: call workspace_read_file on a known file, get correct content."""
    known = tmp_workspace / "hello.txt"
    known.write_text("hello from workspace\n")

    result = device.workspace_read_file("hello.txt")

    assert result["status"] == "ok"
    assert result["content"] == "hello from workspace\n"


def test_read_file_absolute_path_within_root(
    device: WorkspaceDevice, tmp_workspace: Path
) -> None:
    f = tmp_workspace / "sub" / "deep.txt"
    f.parent.mkdir(parents=True)
    f.write_text("deep content")

    result = device.workspace_read_file(str(f))

    assert result["status"] == "ok"
    assert result["content"] == "deep content"


def test_read_file_missing_returns_error(device: WorkspaceDevice) -> None:
    result = device.workspace_read_file("nonexistent.txt")
    assert result["status"] == "error"
    assert "message" in result


def test_read_file_escape_rejected(device: WorkspaceDevice) -> None:
    result = device.workspace_read_file("../../etc/passwd")
    assert result["status"] == "error"
    assert "escapes" in result["message"]


def test_read_file_absolute_escape_rejected(device: WorkspaceDevice) -> None:
    result = device.workspace_read_file("/etc/passwd")
    assert result["status"] == "error"
    assert "escapes" in result["message"]


# ── workspace_write_file ──────────────────────────────────────────────────────


def test_write_file_creates_file(device: WorkspaceDevice, tmp_workspace: Path) -> None:
    result = device.workspace_write_file("output.txt", "written content")

    assert result["status"] == "ok"
    assert (tmp_workspace / "output.txt").read_text() == "written content"


def test_write_file_creates_parent_dirs(
    device: WorkspaceDevice, tmp_workspace: Path
) -> None:
    result = device.workspace_write_file("a/b/c.txt", "nested")

    assert result["status"] == "ok"
    assert (tmp_workspace / "a" / "b" / "c.txt").read_text() == "nested"


def test_write_file_escape_rejected(device: WorkspaceDevice) -> None:
    result = device.workspace_write_file("../../tmp/evil.txt", "bad")
    assert result["status"] == "error"
    assert "escapes" in result["message"]


def test_write_then_read_roundtrip(device: WorkspaceDevice) -> None:
    device.workspace_write_file("roundtrip.txt", "round and round")
    result = device.workspace_read_file("roundtrip.txt")
    assert result["status"] == "ok"
    assert result["content"] == "round and round"


# ── workspace_run_bash ────────────────────────────────────────────────────────


def test_run_bash_executes_in_workspace_root(
    device: WorkspaceDevice, tmp_workspace: Path
) -> None:
    result = device.workspace_run_bash("pwd")

    assert result["status"] == "ok"
    assert result["returncode"] == 0
    # realpath both sides — tmp_workspace may be a symlink on macOS /tmp
    assert os.path.realpath(result["stdout"].strip()) == os.path.realpath(
        str(tmp_workspace)
    )


def test_run_bash_captures_stdout(device: WorkspaceDevice) -> None:
    result = device.workspace_run_bash("echo hello")

    assert result["status"] == "ok"
    assert "hello" in result["stdout"]


def test_run_bash_captures_stderr(device: WorkspaceDevice) -> None:
    result = device.workspace_run_bash("echo err >&2")

    assert result["status"] == "ok"
    assert "err" in result["stderr"]


def test_run_bash_nonzero_exit(device: WorkspaceDevice) -> None:
    result = device.workspace_run_bash("exit 42")

    assert result["status"] == "ok"
    assert result["returncode"] == 42


def test_run_bash_timeout(device: WorkspaceDevice) -> None:
    result = device.workspace_run_bash("sleep 10", timeout_sec=1)

    assert result["status"] == "error"
    assert "timed out" in result["message"]


# ── Announce round-trip: workspace appears in manifest ────────────────────────


class _FakeDevice:
    """Minimal stand-in for the announce broker's device duck-type."""

    def __init__(self, device_id: str, address: str, name: str = "") -> None:
        self.device_id = device_id
        self._address = address
        self._name = name or device_id

    def who_am_i(self) -> dict:
        return {"name": self._name}

    def comms(self) -> dict:
        return {"address": self._address, "mode": "read_write"}


class _FakeRegistry:
    def __init__(self, entries: list[dict]) -> None:
        self._entries = entries

    def list_devices(self) -> list[dict]:
        return self._entries


def _profiles_dir_with(tmp_path: Path, *names: str) -> Path:
    for name in names:
        shutil.copy(CANONICAL_PROFILES / name, tmp_path / name)
    return tmp_path


def test_workspace_appears_in_igor_manifest(tmp_path: Path) -> None:
    """Completion criterion: a test agent (igor) can announce and receive workspace
    tools in its manifest after workspace is added to igor's allowed_devices."""
    from unseen_university.announce.broker import AnnounceBroker
    from unseen_university.announce.envelope import IdentityEnvelope

    profiles_dir = _profiles_dir_with(tmp_path, "igor.yaml")

    registry = _FakeRegistry(
        [
            {"device_id": "workspace", "status": "online"},
            {"device_id": "inference", "status": "online"},
        ]
    )
    live_devices = {
        "workspace": _FakeDevice("workspace", "comms://workspace", "Workspace"),
        "inference": _FakeDevice("inference", "comms://inference", "Inference"),
    }
    broker = AnnounceBroker(
        profiles_dir=profiles_dir, registry=registry, devices=live_devices
    )

    envelope = IdentityEnvelope(
        agent_id="igor",
        instance="wild-0001",
        box="testbox",
        box_n=0,
        pid=9999,
        interface_version="1.0",
        surfaces=["console", "inference"],
        proof={"shared_secret": "test-rack-secret"},
    )

    manifest = broker.resolve_announce(envelope)
    tool_names = {t.name for t in manifest.tools}

    assert (
        "workspace" in tool_names
    ), f"workspace not in manifest tools — got: {tool_names}"
