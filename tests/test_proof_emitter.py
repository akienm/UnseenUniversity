"""Tests for the proof emitter (T-proof-emitter-harness, D-proof-on-close-2026-06-20).

These tests ARE the bootstrap proof for the emitter (it cannot prove itself).
They cover, with REAL subprocess pytest runs against REAL changing code:

  - empirical classification of every red flavor (assert / pytest.fail /
    NameError / ImportError / collection error) — this is the verification the
    advisor insisted on, now permanent;
  - the happy path emits a schema-complete, HEAD-bound proof;
  - collateral red is rejected (stub-first convention);
  - red-that-comes-back-green is rejected (can't fabricate red);
  - green-that-doesn't-pass is rejected;
  - the production git strategy authenticates red from the parent commit and
    rejects when the parent already contains the implementation.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_CLAUDECODE = Path(__file__).resolve().parent.parent / "devlab" / "claudecode"
sys.path.insert(0, str(_CLAUDECODE))

import memory_emit  # noqa: E402
import proof_emitter  # noqa: E402
from proof_emitter import (  # noqa: E402
    ProofError,
    ProofRun,
    _run_proof,
    _run_pytest,
    is_authentic_red,
    prove,
)

_HAS_GIT = shutil.which("git") is not None


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #

IMPL_OK = "def add(a, b):\n    return a + b\n"
IMPL_STUB = "def add(a, b):\n    return None\n"            # present but wrong -> AssertionError
IMPL_NAMEERROR = "def add(a, b):\n    return undefined_symbol_xyz\n"  # collateral
TEST_SRC = (
    "import sample_thing\n"
    "def test_adds():\n"
    "    assert sample_thing.add(2, 3) == 5\n"
)


def _make_sample(tmp_path: Path, impl: str) -> Path:
    d = tmp_path / "sample"
    d.mkdir(exist_ok=True)
    (d / "sample_thing.py").write_text(impl)
    (d / "test_sample.py").write_text(TEST_SRC)
    return d


@contextlib.contextmanager
def _file_swap(target: Path, stub: str):
    """Test-only red strategy: swap the implementation file to ``stub`` for the
    red run, restore after. Yields the cwd to run the red pass in."""
    original = target.read_text()
    target.write_text(stub)
    try:
        yield str(target.parent)
    finally:
        target.write_text(original)


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Point memory_emit at a throwaway store so proofs don't hit the real one."""
    root = tmp_path / "memory"
    monkeypatch.setattr(memory_emit, "MEMORY_ROOT", str(root))
    return root


def _git(root, *args, check=True):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=check)


def _init_repo(root: Path):
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "proof@test")
    _git(root, "config", "user.name", "proof test")


def _commit(root: Path, msg: str):
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", msg)


# --------------------------------------------------------------------------- #
# 1. Empirical classification — verify the plugin, don't assume it
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("body,expect_outcome,expect_exc,expect_authentic", [
    ("    assert (lambda a, b: None)(2, 3) == 5", "failed", "AssertionError", True),
    ("    import pytest; pytest.fail('boom')",     "failed", "Failed",        True),
    ("    assert undefined_name_xyz(2, 3) == 5",   "failed", "NameError",     False),
    ("    import totally_missing_xyz",             "failed", "ModuleNotFoundError", False),
])
def test_plugin_classifies_call_failures(tmp_path, body, expect_outcome, expect_exc, expect_authentic):
    t = tmp_path / "test_probe.py"
    t.write_text("def test_case():\n" + body + "\n")
    run = _run_pytest("test_probe.py::test_case", cwd=str(tmp_path))
    assert run.outcome == expect_outcome
    assert run.exc_type == expect_exc
    assert is_authentic_red(run) is expect_authentic


def test_plugin_classifies_collection_error(tmp_path):
    t = tmp_path / "test_collect.py"
    t.write_text("import totally_missing_at_collection_xyz\ndef test_x():\n    assert True\n")
    run = _run_pytest("test_collect.py::test_x", cwd=str(tmp_path))
    assert run.outcome == "error"
    assert run.exc_type == "CollectionError"
    assert is_authentic_red(run) is False


def test_plugin_classifies_pass(tmp_path):
    t = tmp_path / "test_pass.py"
    t.write_text("def test_ok():\n    assert 1 == 1\n")
    run = _run_pytest("test_pass.py::test_ok", cwd=str(tmp_path))
    assert run.outcome == "passed"
    assert is_authentic_red(run) is False


def test_is_authentic_red_unit():
    assert is_authentic_red(ProofRun("x", "failed", "AssertionError", 1, "")) is True
    assert is_authentic_red(ProofRun("x", "failed", "Failed", 1, "")) is True
    assert is_authentic_red(ProofRun("x", "failed", "NameError", 1, "")) is False
    assert is_authentic_red(ProofRun("x", "passed", None, 0, "")) is False
    assert is_authentic_red(ProofRun("x", "error", "CollectionError", 2, "")) is False


# --------------------------------------------------------------------------- #
# 2. _run_proof core — happy path + all reject paths (injected file-swap red)
# --------------------------------------------------------------------------- #

def test_happy_path_emits_complete_proof(tmp_path, isolated_store):
    d = _make_sample(tmp_path, IMPL_OK)
    rec = _run_proof(
        thing="add() sums two numbers",
        intention="add(2, 3) returns 5",
        test="test_sample.py::test_adds",
        ticket="T-proof-emitter-harness",
        narrative="bootstrap sample proof",
        why="CP1 — done means proven",
        red_strategy=_file_swap(d / "sample_thing.py", IMPL_STUB),
        commit="abc123",
        repo_root=str(d),
    )
    proof = json.loads(Path(rec["path"]).read_text())
    assert proof["kind"] == "proof"
    assert proof["links"]["commits"] == ["abc123"]          # canonical commit home
    assert proof["links"]["tickets"] == ["T-proof-emitter-harness"]
    b = proof["body"]
    for key in ("thing", "intention", "test", "gates", "commit", "ticket", "narrative", "why"):
        assert key in b, f"missing proof field: {key}"
    assert b["commit"] == "abc123"
    ev = b["gates"][0]["evidence"]
    assert ev["red_run"]["authentic_red"] is True
    assert ev["red_run"]["exc_type"] == "AssertionError"
    assert ev["green_run"]["outcome"] == "passed"


def test_collateral_red_is_rejected(tmp_path, isolated_store):
    d = _make_sample(tmp_path, IMPL_OK)
    with pytest.raises(ProofError, match="collateral error|stub-first"):
        _run_proof(
            thing="add", intention="add(2,3)==5", test="test_sample.py::test_adds",
            ticket=None, narrative="", why="",
            red_strategy=_file_swap(d / "sample_thing.py", IMPL_NAMEERROR),
            commit="abc123", repo_root=str(d),
        )


def test_red_that_comes_back_green_is_rejected(tmp_path, isolated_store):
    d = _make_sample(tmp_path, IMPL_OK)
    # nullcontext yields the impl tree unchanged -> "red" run passes -> reject.
    with pytest.raises(ProofError, match="could not generate authentic red"):
        _run_proof(
            thing="add", intention="add(2,3)==5", test="test_sample.py::test_adds",
            ticket=None, narrative="", why="",
            red_strategy=contextlib.nullcontext(str(d)),
            commit="abc123", repo_root=str(d),
        )


def test_green_that_does_not_pass_is_rejected(tmp_path, isolated_store):
    d = _make_sample(tmp_path, IMPL_STUB)   # impl already wrong -> green fails
    with pytest.raises(ProofError, match="green run did not pass"):
        _run_proof(
            thing="add", intention="add(2,3)==5", test="test_sample.py::test_adds",
            ticket=None, narrative="", why="",
            red_strategy=contextlib.nullcontext(str(d)),
            commit="abc123", repo_root=str(d),
        )


def test_no_proof_emitted_on_rejection(tmp_path, isolated_store):
    d = _make_sample(tmp_path, IMPL_OK)
    with pytest.raises(ProofError):
        _run_proof(
            thing="add", intention="add(2,3)==5", test="test_sample.py::test_adds",
            ticket=None, narrative="", why="",
            red_strategy=contextlib.nullcontext(str(d)),
            commit="abc123", repo_root=str(d),
        )
    proofs_dir = isolated_store / "proofs"
    assert not proofs_dir.exists() or not list(proofs_dir.glob("*.json"))


# --------------------------------------------------------------------------- #
# 3. Production git strategy — derives red from the parent commit
# --------------------------------------------------------------------------- #

@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
def test_git_strategy_happy_path(tmp_path, isolated_store):
    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)
    # commit 1: stub impl only (no test yet — exercises the test-overlay path)
    (root / "sample_thing.py").write_text(IMPL_STUB)
    _commit(root, "stub")
    # commit 2 (HEAD): real impl + the test
    (root / "sample_thing.py").write_text(IMPL_OK)
    (root / "test_sample.py").write_text(TEST_SRC)
    _commit(root, "impl + test")
    head = _git(root, "rev-parse", "HEAD").stdout.strip()

    rec = prove(
        "add() sums", "add(2,3)==5", "test_sample.py::test_adds",
        ticket="T-proof-emitter-harness", narrative="git path", why="CP1",
        repo_root=str(root),
    )
    assert rec["commit"] == head
    proof = json.loads(Path(rec["path"]).read_text())
    assert proof["links"]["commits"] == [head]
    assert proof["body"]["gates"][0]["evidence"]["red_run"]["authentic_red"] is True


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
def test_git_strategy_rejects_when_parent_already_has_impl(tmp_path, isolated_store):
    root = tmp_path / "repo2"
    root.mkdir()
    _init_repo(root)
    # commit 1: impl + test already present (so HEAD~1 passes the test)
    (root / "sample_thing.py").write_text(IMPL_OK)
    (root / "test_sample.py").write_text(TEST_SRC)
    _commit(root, "impl + test")
    # commit 2 (HEAD): unrelated no-op change
    (root / "README").write_text("noop\n")
    _commit(root, "noop")

    with pytest.raises(ProofError, match="could not generate authentic red"):
        prove("add", "add(2,3)==5", "test_sample.py::test_adds",
              repo_root=str(root))


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
def test_git_strategy_rejects_dirty_tree(tmp_path, isolated_store):
    root = tmp_path / "repo3"
    root.mkdir()
    _init_repo(root)
    (root / "sample_thing.py").write_text(IMPL_STUB)
    _commit(root, "stub")
    (root / "sample_thing.py").write_text(IMPL_OK)
    (root / "test_sample.py").write_text(TEST_SRC)
    _commit(root, "impl + test")
    # uncommitted change -> proof would bind to HEAD but run against dirty tree
    (root / "sample_thing.py").write_text(IMPL_OK + "\n# uncommitted edit\n")
    with pytest.raises(ProofError, match="working tree is dirty"):
        prove("add", "add(2,3)==5", "test_sample.py::test_adds", repo_root=str(root))
