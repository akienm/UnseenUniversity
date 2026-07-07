"""Red->green proof for merge-time proof emission (T-builder-merge-time-proof).

The offload-loop crux: a branch-builder closes shipped-unproven because its impl is on
an unmerged branch. Once CC merges that branch to HEAD, `emit_merge_proof` must emit a
HEAD-valid red->green proof so proof_store.best_valid_proof finds it and the
proof-on-close gate can flip the ticket to proven. THIS is what makes builder
throughput actually reduce CC's queue instead of shifting eyeball-validation to CC.

Proven with a REAL local git merge (no mocks): a branch that MODIFIES an existing impl
file + adds a test is merged, then emit_merge_proof runs against the pre-merge parent.
The proof must be valid AND the close-gate must flip proven False->True.

Red form (a hollow emit_merge_proof that reports valid without emitting a real
HEAD-valid proof): best_valid_proof returns None -> valid is False -> the two
assertions below fail.
"""

import os
import subprocess
import sys

import pytest

_DEVLAB = os.path.join(os.path.dirname(__file__), "..", "devlab", "claudecode")
sys.path.insert(0, os.path.abspath(_DEVLAB))

from builder_merge_proof import emit_merge_proof  # noqa: E402
from unseen_university import proof_store  # noqa: E402
import cc_queue  # noqa: E402
import memory_emit  # noqa: E402  (its MEMORY_ROOT is frozen at import — patch it below)


def _git(args, cwd):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, f"git {args} failed: {r.stderr}"
    return r.stdout.strip()


@pytest.fixture
def merged_repo(tmp_path, monkeypatch):
    """A git repo where a builder branch (impl MODIFIED + test ADDED) has been merged
    to HEAD. Proof/ticket store redirected to an isolated dir. Returns (repo, parent_ref)."""
    mem = tmp_path / "mem"
    # Two redirects, both required: proof_store READS via the live memory_root() (env),
    # but memory_emit WRITES via a MEMORY_ROOT frozen at import time (before this
    # fixture ran) — patch the module attr too, or the proof lands in the REAL store.
    monkeypatch.setenv("UU_MEMORY_ROOT", str(mem))
    monkeypatch.setattr(memory_emit, "MEMORY_ROOT", str(mem))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(["init", "-q"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    # Baseline: a WRONG impl on main (add returns 0). Standalone module — no
    # unseen_university import, so the editable finder is irrelevant to this test.
    (repo / "calc.py").write_text("def add(a, b):\n    return 0\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "baseline"], repo)
    parent_ref = _git(["rev-parse", "HEAD"], repo)
    main_branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], repo)  # main/master varies
    # Builder branch: FIX the impl (modify calc.py) + ADD a test.
    _git(["checkout", "-q", "-b", "aider/T-merge-x"], repo)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "fix add + test"], repo)
    # CC validates + merges the branch back to the trunk (real merge commit at HEAD).
    _git(["checkout", "-q", main_branch], repo)
    _git(["merge", "--no-ff", "-q", "-m", "merge builder branch", "aider/T-merge-x"], repo)
    return repo, parent_ref


def test_merge_emits_headvalid_proof(merged_repo):
    repo, parent_ref = merged_repo
    res = emit_merge_proof(
        "T-merge-x", "test_calc.py::test_add",
        thing="calc.add returns the sum",
        intention="add(a,b) returns a+b, proven when the merged branch is at HEAD",
        parent_ref=parent_ref, repo_root=str(repo),
    )
    assert res["valid"] is True, f"merge proof not HEAD-valid; rejections={res['rejections']}"
    assert res["proof_id"]


def test_gate_flips_shipped_unproven_to_proven(merged_repo):
    repo, parent_ref = merged_repo
    emit_merge_proof(
        "T-merge-x", "test_calc.py::test_add",
        thing="calc.add returns the sum",
        intention="add(a,b) returns a+b",
        parent_ref=parent_ref, repo_root=str(repo),
    )
    # The proof-on-close gate now finds the merge proof and returns proven=True —
    # the flip that shipped-unproven -> proven a re-close would apply.
    allowed, annotations, _msg = cc_queue._proof_gate({"id": "T-merge-x"}, repo_root=str(repo))
    assert allowed is True
    assert annotations.get("proven") is True
