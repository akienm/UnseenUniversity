"""
test_minion_workspace.py — Unit tests for MinionWorkspace.

Uses a real local git repo as the "origin" to avoid network dependency.
All clones land in tmp_path and are cleaned up by pytest.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devlab.claudecode.minion_workspace import MinionWorkspace, _WORKSPACE_BASE

# ── Helpers ───────────────────────────────────────────────────────────────────


def _git(args: list[str], cwd: Path) -> str:
    r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, check=True)
    return r.stdout.strip()


@pytest.fixture
def origin_repo(tmp_path) -> Path:
    """Create a bare-minimum local git repo to clone from."""
    repo = tmp_path / "origin"
    repo.mkdir()
    _git(["git", "init", "-b", "main"], cwd=repo)
    _git(["git", "config", "user.email", "test@example.com"], cwd=repo)
    _git(["git", "config", "user.name", "Test"], cwd=repo)
    (repo / "README.md").write_text("# test repo\n")
    _git(["git", "add", "README.md"], cwd=repo)
    _git(["git", "commit", "-m", "init"], cwd=repo)
    return repo


@pytest.fixture
def workspace(tmp_path, origin_repo, monkeypatch) -> MinionWorkspace:
    """MinionWorkspace pointed at a tmp origin with workspace root in tmp_path."""
    monkeypatch.setattr(
        "devlab.claudecode.minion_workspace._WORKSPACE_BASE", tmp_path / "dc"
    )
    return MinionWorkspace("cc1", repo_origin=str(origin_repo))


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestWorkspacePath:
    def test_path_structure(self, workspace, tmp_path):
        assert (
            workspace.workspace_path
            == tmp_path / "dc" / "cc1" / "workspace" / "TheIgors"
        )

    def test_not_cloned_before_setup(self, workspace):
        assert not workspace.is_cloned()


class TestSetup:
    def test_setup_clones_repo(self, workspace):
        workspace.setup()
        assert workspace.is_cloned()
        assert (workspace.workspace_path / "README.md").exists()

    def test_setup_idempotent(self, workspace):
        workspace.setup()
        workspace.setup()  # second call should not raise
        assert workspace.is_cloned()

    def test_setup_returns_workspace_path(self, workspace):
        result = workspace.setup()
        assert result == workspace.workspace_path


class TestBranch:
    def test_creates_feature_branch(self, workspace):
        workspace.setup()
        name = workspace.branch("T-my-ticket")
        assert name == "minion/cc1/T-my-ticket"
        assert workspace.current_branch() == "minion/cc1/T-my-ticket"

    def test_branch_name_format(self, workspace):
        assert workspace.branch_name("T-foo-bar") == "minion/cc1/T-foo-bar"

    def test_raises_when_not_cloned(self, workspace):
        with pytest.raises(RuntimeError, match="setup"):
            workspace.branch("T-ticket")

    def test_branch_recreates_if_stale(self, workspace):
        workspace.setup()
        workspace.branch("T-old")
        # Add a commit on the branch so it's not trivial
        (workspace.workspace_path / "change.txt").write_text("x\n")
        _git(["git", "add", "change.txt"], cwd=workspace.workspace_path)
        _git(
            [
                "git",
                "-c",
                "user.email=t@t.com",
                "-c",
                "user.name=T",
                "commit",
                "-m",
                "branch commit",
            ],
            cwd=workspace.workspace_path,
        )
        # Re-create the same ticket branch — should not error
        workspace.branch("T-old")
        assert workspace.current_branch() == "minion/cc1/T-old"


class TestMergeBack:
    def test_merges_feature_into_main(self, workspace):
        workspace.setup()
        workspace.branch("T-feature")
        # Commit something on the branch
        (workspace.workspace_path / "feature.txt").write_text("feature\n")
        _git(["git", "add", "feature.txt"], cwd=workspace.workspace_path)
        _git(
            [
                "git",
                "-c",
                "user.email=t@t.com",
                "-c",
                "user.name=T",
                "commit",
                "-m",
                "add feature",
            ],
            cwd=workspace.workspace_path,
        )
        output = workspace.merge_back()
        # Back on main
        assert workspace.current_branch() == "main"
        # File is visible on main
        assert (workspace.workspace_path / "feature.txt").exists()
        assert "Merge" in output or "merge" in output.lower() or output == ""

    def test_raises_when_not_cloned(self, workspace):
        with pytest.raises(RuntimeError, match="setup"):
            workspace.merge_back()


class TestClean:
    def test_returns_to_main(self, workspace):
        workspace.setup()
        workspace.branch("T-dirty")
        workspace.clean()
        assert workspace.current_branch() == "main"

    def test_discards_uncommitted_changes(self, workspace):
        workspace.setup()
        dirty = workspace.workspace_path / "dirty.txt"
        dirty.write_text("untracked\n")
        workspace.clean()
        assert not dirty.exists()

    def test_clean_noop_when_not_cloned(self, workspace):
        workspace.clean()  # must not raise


class TestDestroy:
    def test_removes_clone(self, workspace):
        workspace.setup()
        assert workspace.is_cloned()
        workspace.destroy()
        assert not workspace.workspace_path.exists()

    def test_destroy_noop_when_not_cloned(self, workspace):
        workspace.destroy()  # must not raise
