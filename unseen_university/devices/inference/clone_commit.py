"""
clone_commit.py — commit-per-edit granularity in the throwaway build clone (T-aider-port-commit-per-edit).

Port of aider base_coder.auto_commit / dirty_commit. Each applied edit becomes a distinct commit
in the throwaway clone, and a dirty file is snapshotted before it is touched. This is NOT a
0-edits fixer — it is a measurement/safety enabler: it feeds corpus_verdict's temporal firewall
(the 'later commit on the same paths' check) at EDIT granularity, and gives fine-grained rollback.
Verdicts reach git history before any hypothesis exists (the grounding-spine asymmetry).

Fail-soft by construction: outside a git work tree (e.g. a unit-test tmp dir, or if git is
unhappy) every method is a no-op — commit-per-edit must never break an apply. Scope is the clone
ONLY; nothing here touches the merge/main path.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, check=False,
    )


def _is_git_repo(cwd: Path) -> bool:
    try:
        return _git(cwd, "rev-parse", "--is-inside-work-tree").returncode == 0
    except (OSError, ValueError):
        return False


class CloneCommitter:
    """Commit each applied edit in the clone; dirty-commit a dirty file before it is edited.

    ``commits`` counts commits actually made (the fixture asserts commit-count == applied-count).
    Construct one per attempt with the clone dir; pass it to ``apply_blocks_to_dir``. Outside a git
    repo it self-disables and every call is a harmless no-op.
    """

    def __init__(self, cwd: str | Path) -> None:
        self.cwd = Path(cwd)
        self.enabled = _is_git_repo(self.cwd)
        self.commits = 0

    def before(self, rel_path: str) -> None:
        """Snapshot a pre-existing DIRTY file before the edit touches it (aider dirty_commit)."""
        if not self.enabled:
            return
        status = _git(self.cwd, "status", "--porcelain", "--", rel_path)
        if status.returncode == 0 and status.stdout.strip():
            self._commit(rel_path, f"aider: dirty snapshot of {rel_path} before edit")

    def after(self, rel_path: str) -> None:
        """Commit one applied edit in the clone (aider auto_commit, edit granularity)."""
        if not self.enabled:
            return
        self._commit(rel_path, f"aider: edit {rel_path}")

    def _commit(self, rel_path: str, message: str) -> None:
        add = _git(self.cwd, "add", "--", rel_path)
        if add.returncode != 0:
            log.warning("clone_commit: git add failed for %s: %s", rel_path, add.stderr.strip())
            return
        commit = _git(self.cwd, "commit", "--no-verify", "-m", message, "--", rel_path)
        if commit.returncode == 0:
            self.commits += 1
        else:
            # Nothing staged / identity unset / hook refusal — fail-soft, never break the apply.
            log.debug("clone_commit: git commit skipped for %s: %s", rel_path, commit.stderr.strip())
