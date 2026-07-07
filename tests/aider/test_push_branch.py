"""Red->green proof for aider push-to-origin (T-aider-real-uu-ticket-proof, surface 3).

The aider builder builds on a throwaway clone; the work branch must REACH CC for
validation. `_push_branch` delivers that branch to a shared remote, guarded so a
builder can never push the trunk (branch-not-main lesson, 124553ee).

Proven offline against a LOCAL BARE remote — no network. Red form:
  - a hollow `_push_branch` that reports ok without pushing fails
    `test_push_delivers_branch_to_remote` (the branch is absent in the bare repo).
  - a hollow one that skips the guard fails `test_refuses_to_push_trunk`.
"""

import subprocess
from pathlib import Path

from unseen_university.devices.aider.runner import _push_branch

_WORK_BRANCH = "aider/T-proof-123"


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _make_repo_and_bare_remote(tmp_path: Path):
    """A clone with a committed work branch + a bare repo to serve as the shared remote."""
    src = tmp_path / "src"
    src.mkdir()
    _git(["init", "--quiet"], src)
    _git(["config", "user.email", "t@t"], src)
    _git(["config", "user.name", "t"], src)
    (src / "f.txt").write_text("base\n")
    _git(["add", "-A"], src)
    _git(["commit", "--quiet", "-m", "base"], src)
    _git(["checkout", "--quiet", "-b", _WORK_BRANCH], src)
    (src / "f.txt").write_text("edited on branch\n")
    _git(["add", "-A"], src)
    _git(["commit", "--quiet", "-m", "work"], src)

    bare = tmp_path / "remote.git"
    _git(["init", "--bare", "--quiet", str(bare)], tmp_path)
    return src, bare


def _remote_branches(bare: Path):
    r = _git(["branch", "--list"], bare)
    return {ln.strip().lstrip("* ").strip() for ln in r.stdout.splitlines() if ln.strip()}


def test_push_delivers_branch_to_remote(tmp_path):
    src, bare = _make_repo_and_bare_remote(tmp_path)
    ok, note = _push_branch(src, _WORK_BRANCH, str(bare))
    assert ok is True, f"push should succeed; note={note!r}"
    assert _WORK_BRANCH in _remote_branches(bare), (
        f"work branch did not reach the remote; remote has {_remote_branches(bare)}")


def test_refuses_to_push_trunk(tmp_path):
    src, bare = _make_repo_and_bare_remote(tmp_path)
    for trunk in ("main", "master"):
        ok, note = _push_branch(src, trunk, str(bare))
        assert ok is False, f"must refuse to push {trunk!r}"
        assert "refused" in note
    # and the guard means the trunk never landed on the remote
    assert "main" not in _remote_branches(bare)
    assert "master" not in _remote_branches(bare)


def test_push_failure_is_reported_not_raised(tmp_path):
    src, _bare = _make_repo_and_bare_remote(tmp_path)
    ok, note = _push_branch(src, _WORK_BRANCH, str(tmp_path / "does-not-exist.git"))
    assert ok is False
    assert "push failed" in note
