"""
Proof for commit-per-edit in the throwaway clone (T-aider-port-commit-per-edit).

Coarse rollback + coarse temporal resolution for corpus_verdict's firewall is the problem;
committing each applied edit (and dirty-snapshotting a file before touching it) is the fix. The
discriminator: commit count == applied-edit count on a git-backed fixture, and a pre-dirty file
is committed BEFORE the edit lands.
"""

from __future__ import annotations

import subprocess

from unseen_university.devices.inference.block_apply import apply_blocks_to_dir
from unseen_university.devices.inference.clone_commit import CloneCommitter


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False)


def _init_repo(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "t@t.t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "a.py").write_text("a = 1\n")
    (tmp_path / "b.py").write_text("b = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "init")


def _count(tmp_path):
    return int(_git(tmp_path, "rev-list", "--count", "HEAD").stdout.strip())


_TWO_EDITS = (
    "a.py\n<<<<<<< SEARCH\na = 1\n=======\na = 2\n>>>>>>> REPLACE\n\n"
    "b.py\n<<<<<<< SEARCH\nb = 1\n=======\nb = 2\n>>>>>>> REPLACE\n"
)


# ── THE PROOF NODE — commit count == applied-edit count ───────────────────────

def test_commit_count_equals_applied_edit_count(tmp_path):
    _init_repo(tmp_path)
    base = _count(tmp_path)

    committer = CloneCommitter(tmp_path)
    result = apply_blocks_to_dir(_TWO_EDITS, tmp_path, committer=committer)

    assert result.applied == ["a.py", "b.py"], f"both edits should apply: {result}"
    assert _count(tmp_path) - base == 2, (
        "each applied edit must produce a distinct commit in the clone; "
        f"got {_count(tmp_path) - base} commits for 2 edits"
    )
    assert committer.commits == 2


def test_dirty_file_committed_before_edit(tmp_path):
    """A file dirty from prior (uncommitted) work is snapshotted BEFORE the edit touches it."""
    _init_repo(tmp_path)
    # Make a.py dirty without committing — simulates prior in-clone work.
    (tmp_path / "a.py").write_text("a = 1\n# uncommitted prior work\n")
    base = _count(tmp_path)

    committer = CloneCommitter(tmp_path)
    apply_blocks_to_dir(
        "a.py\n<<<<<<< SEARCH\na = 1\n=======\na = 99\n>>>>>>> REPLACE\n",
        tmp_path, committer=committer,
    )
    # One dirty-snapshot commit + one edit commit = 2 new commits.
    assert _count(tmp_path) - base == 2, f"expected dirty-snapshot + edit commits, got {_count(tmp_path) - base}"
    # The dirty snapshot preserved the prior work as its own history point.
    logs = _git(tmp_path, "log", "--oneline").stdout
    assert "dirty snapshot" in logs and "edit a.py" in logs


def test_committer_noop_outside_git_repo(tmp_path):
    """Outside a git work tree the committer self-disables — apply still works, no commits, no error."""
    committer = CloneCommitter(tmp_path)  # tmp_path is NOT a git repo
    assert committer.enabled is False
    (tmp_path / "a.py").write_text("a = 1\n")
    result = apply_blocks_to_dir(
        "a.py\n<<<<<<< SEARCH\na = 1\n=======\na = 2\n>>>>>>> REPLACE\n",
        tmp_path, committer=committer,
    )
    assert result.applied == ["a.py"] and committer.commits == 0
