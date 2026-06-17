"""
minion_workspace.py — Per-minion git workspace: clone → branch → work → merge-back.

When multiple minions work concurrently, the single-working-tree assumption breaks
(stash/pull/pop only isolates serially). Each minion gets its own local clone with
a feature branch per ticket; merge-back is to local main, push to GitHub is explicit.

Workspace layout:
    ~/.unseen_university/<instance>/workspace/TheIgors/   ← clone root

Branch naming:
    minion/<instance>/<ticket-id>

Usage:
    ws = MinionWorkspace("cc1")
    ws.setup()                                  # clone if absent, fetch if present
    branch = ws.branch("T-my-ticket")           # create + checkout feature branch
    # ... do work in ws.workspace_path ...
    output = ws.merge_back()                    # merge → local main
    ws.clean()                                  # return to clean main

All git operations run in the workspace clone, never in the caller's cwd.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_DEFAULT_REPO_ORIGIN = str(Path(__file__).parent.parent.parent.resolve())
_WORKSPACE_BASE = Path.home() / ".unseen_university"


def _run(cmd: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=check,
    )


class MinionWorkspace:
    """Per-minion git workspace with clone + feature-branch + merge-back support."""

    def __init__(self, instance: str, repo_origin: str | None = None):
        """
        instance:    minion identifier, e.g. "cc1", "igor-main", "haiku-worker"
        repo_origin: path or URL of the source repo (defaults to ~/TheIgors)
        """
        self.instance = instance
        self.repo_origin = repo_origin or _DEFAULT_REPO_ORIGIN

    @property
    def workspace_path(self) -> Path:
        """~/.unseen_university/<instance>/workspace/UnseenUniversity"""
        return _WORKSPACE_BASE / self.instance / "workspace" / "UnseenUniversity"

    def is_cloned(self) -> bool:
        return (self.workspace_path / ".git").exists()

    def setup(self) -> Path:
        """Ensure workspace clone exists. Clones from repo_origin if absent; fetches + fast-forwards main if present."""
        if not self.is_cloned():
            self.workspace_path.parent.mkdir(parents=True, exist_ok=True)
            _run(
                ["git", "clone", self.repo_origin, str(self.workspace_path)],
                cwd=self.workspace_path.parent,
            )
        else:
            _run(["git", "fetch", "origin"], cwd=self.workspace_path)
            _run(
                ["git", "checkout", "main"],
                cwd=self.workspace_path,
                check=False,
            )
            _run(
                ["git", "merge", "--ff-only", "origin/main"],
                cwd=self.workspace_path,
                check=False,
            )
        return self.workspace_path

    def branch_name(self, ticket_id: str) -> str:
        return f"minion/{self.instance}/{ticket_id}"

    def branch(self, ticket_id: str) -> str:
        """Create and checkout feature branch minion/<instance>/<ticket-id>. Returns branch name."""
        if not self.is_cloned():
            raise RuntimeError(
                f"Workspace not cloned at {self.workspace_path} — call setup() first"
            )
        name = self.branch_name(ticket_id)
        # Delete stale branch of the same name if it exists
        _run(
            ["git", "checkout", "main"],
            cwd=self.workspace_path,
            check=False,
        )
        _run(
            ["git", "branch", "-D", name],
            cwd=self.workspace_path,
            check=False,
        )
        _run(
            ["git", "checkout", "-b", name],
            cwd=self.workspace_path,
        )
        return name

    def current_branch(self) -> str:
        """Return the active branch name in this workspace."""
        if not self.is_cloned():
            return "(not cloned)"
        result = _run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=self.workspace_path,
        )
        return result.stdout.strip()

    def merge_back(self, target: str = "main") -> str:
        """Merge current feature branch into target. Returns merge stdout+stderr."""
        if not self.is_cloned():
            raise RuntimeError(
                f"Workspace not cloned at {self.workspace_path} — call setup() first"
            )
        feature = self.current_branch()
        _run(["git", "checkout", target], cwd=self.workspace_path)
        result = _run(
            ["git", "merge", "--no-ff", feature, "-m", f"Merge {feature}"],
            cwd=self.workspace_path,
        )
        return (result.stdout + result.stderr).strip()

    def clean(self) -> None:
        """Return workspace to clean main: checkout main, discard uncommitted changes."""
        if not self.is_cloned():
            return
        _run(
            ["git", "checkout", "main"],
            cwd=self.workspace_path,
            check=False,
        )
        _run(
            ["git", "reset", "--hard", "HEAD"],
            cwd=self.workspace_path,
            check=False,
        )
        _run(
            ["git", "clean", "-fd"],
            cwd=self.workspace_path,
            check=False,
        )

    def destroy(self) -> None:
        """Remove the workspace clone entirely. Irreversible — all uncommitted work lost."""
        import shutil

        if self.workspace_path.exists():
            shutil.rmtree(self.workspace_path)
