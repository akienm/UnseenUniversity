"""Proof-on-close gate (D-proof-on-close-2026-06-20, T-ticket-close-requires-proof).

The CP1 consumption gate: a ticket reaches `closed` one of two honest ways —
pointing at a HEAD-valid proof, or closing `shipped-unproven` while NAMING THE
MISSING PROOF-LEVER. A bare proofless close is refused. No load-bearing
discriminator (dropped as an escape hatch).

Covers the ticket's five close attempts:
  1. no proof                  -> rejected
  2. stale-commit / drifted    -> rejected
  3. HEAD-valid proof          -> allowed (proven=True)
  4. shipped-unproven + reason -> allowed, renders distinctly
  5. shipped-unproven, NO reason -> rejected (a silent 'done' by another name)
plus effective_status() rendering an unproven close distinctly from a proven one.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# proof_emitter resolves `import memory_emit` as a BARE module (devlab/claudecode
# on sys.path), which is a DIFFERENT object than devlab.claudecode.memory_emit.
# Import + monkeypatch the same bare object the emitter writes through.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "devlab" / "claudecode"))

import memory_emit  # noqa: E402
from proof_emitter import prove  # noqa: E402
import devlab.claudecode.cc_queue as cq  # noqa: E402
from unseen_university import proof_store  # noqa: E402
from unseen_university.ticket_status import effective_status, status_label  # noqa: E402

_HAS_GIT = subprocess.run(["git", "--version"], capture_output=True).returncode == 0

IMPL_STUB = "def add(a, b):\n    return None\n"
IMPL_OK = "def add(a, b):\n    return a + b\n"
TEST_SRC = (
    "import sample_thing\n"
    "def test_adds():\n"
    "    assert sample_thing.add(2, 3) == 5\n"
)


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                          text=True, check=True)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Align BOTH write side (memory_emit) and read side (proof_store) on one
    throwaway store, so prove() emits where proof_store looks."""
    root = tmp_path / "memory"
    monkeypatch.setattr(memory_emit, "MEMORY_ROOT", str(root))
    monkeypatch.setenv("UU_MEMORY_ROOT", str(root))
    return root


@pytest.fixture
def proven_repo(tmp_path):
    """A repo where T-x is genuinely proven: stub commit -> impl+test commit."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "p@t")
    _git(root, "config", "user.name", "p t")
    (root / "sample_thing.py").write_text(IMPL_STUB)
    _git(root, "add", "-A"); _git(root, "commit", "-q", "-m", "stub")
    (root / "sample_thing.py").write_text(IMPL_OK)
    (root / "test_sample.py").write_text(TEST_SRC)
    _git(root, "add", "-A"); _git(root, "commit", "-q", "-m", "impl + test")
    return root


# ── proof_store: lookup + validity ──────────────────────────────────────────

@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
def test_head_valid_proof_is_found_and_validates(store, proven_repo):
    prove("add", "add(2,3)==5", "test_sample.py::test_adds",
          ticket="T-x", repo_root=str(proven_repo))
    found = proof_store.find_for_ticket("T-x")
    assert len(found) == 1
    ok, reason = proof_store.validate(found[0], str(proven_repo))
    assert ok, reason
    best, rej = proof_store.best_valid_proof("T-x", str(proven_repo))
    assert best is not None and rej == []


def test_no_proof_is_rejected(store, tmp_path):
    best, rej = proof_store.best_valid_proof("T-nonexistent", str(tmp_path))
    assert best is None
    assert any("no proof" in r for r in rej)


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
def test_drifted_impl_is_rejected_as_stale(store, proven_repo):
    prove("add", "add(2,3)==5", "test_sample.py::test_adds",
          ticket="T-x", repo_root=str(proven_repo))
    # Drift the implementation AFTER proving -> a later commit touches impl_paths.
    (proven_repo / "sample_thing.py").write_text(IMPL_OK + "\n# drift\n")
    _git(proven_repo, "add", "-A"); _git(proven_repo, "commit", "-q", "-m", "drift")
    best, rej = proof_store.best_valid_proof("T-x", str(proven_repo))
    assert best is None
    assert any("drift" in r.lower() for r in rej)


@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
def test_proof_without_impl_paths_is_invalid(store, proven_repo):
    # A proof predating impl_paths recording can't be drift-scoped -> invalid.
    # Use a REAL reachable commit so we fail on the impl_paths check, not on
    # reachability (which is checked first).
    head = _git(proven_repo, "rev-parse", "HEAD").stdout.strip()
    proof = {"body": {"commit": head, "impl_paths": []},
             "links": {"commits": [head]}}
    ok, reason = proof_store.validate(proof, str(proven_repo))
    assert not ok and "impl_paths" in reason


# ── _proof_gate: the decision ───────────────────────────────────────────────

@pytest.mark.skipif(not _HAS_GIT, reason="git not available")
def test_gate_allows_proven_close(store, proven_repo):
    prove("add", "add(2,3)==5", "test_sample.py::test_adds",
          ticket="T-x", repo_root=str(proven_repo))
    allowed, ann, msg = cq._proof_gate({"id": "T-x"}, repo_root=str(proven_repo))
    assert allowed
    assert ann["proven"] is True
    assert "proof OK" in msg


def test_gate_rejects_proofless_close(store, tmp_path):
    allowed, ann, msg = cq._proof_gate({"id": "T-none"}, repo_root=str(tmp_path))
    assert not allowed
    assert ann == {}
    assert "PROOF REQUIRED" in msg


def test_gate_allows_shipped_unproven_with_reason():
    allowed, ann, msg = cq._proof_gate(
        {"id": "T-conceptual"},
        unproven_reason="no lever yet to prove this conceptual claim",
    )
    assert allowed
    assert ann["proven"] is False
    assert "no lever yet" in ann["unproven_reason"]


@pytest.mark.parametrize("reason", ["", "   ", "\t"])
def test_gate_rejects_shipped_unproven_without_reason(reason):
    allowed, ann, msg = cq._proof_gate({"id": "T-x"}, unproven_reason=reason)
    assert not allowed
    assert "MISSING PROOF-LEVER" in msg


# ── effective_status: distinct rendering ────────────────────────────────────

def test_unproven_close_renders_distinctly():
    assert effective_status({"status": "closed", "proven": False}, []) == "shipped-unproven"
    assert effective_status({"status": "closed", "proven": True}, []) == "closed"
    # No proven field (legacy closed ticket) stays plain closed — not mislabelled.
    assert effective_status({"status": "closed"}, []) == "closed"
    assert "unproven" in status_label("shipped-unproven").lower()


# ── cmd_close: flag parsing + rejection end-to-end ──────────────────────────

def _stub_close_io(monkeypatch, ticket):
    monkeypatch.setattr(cq, "_load", lambda: [ticket])
    monkeypatch.setattr(cq, "_save", lambda t: None)
    monkeypatch.setattr(cq, "_log", lambda e: None)
    monkeypatch.setattr(cq, "_compute_cost_usd", lambda tid: None)
    monkeypatch.setattr(cq, "_decision_rollup", lambda tasks, did: None)
    monkeypatch.setattr(cq, "_ungate_dependents", lambda tasks, tid: None)
    monkeypatch.setattr(cq, "_prepend_closed_ticket", lambda tid, title: None)
    monkeypatch.setattr(cq, "_close_igor_goal", lambda tid: None)
    monkeypatch.setattr(cq, "_classifier_clear_in_flight", lambda tid: None)
    monkeypatch.setattr(cq, "_annotator_delta_update", lambda tid: None)
    monkeypatch.setattr(cq, "_record_ticket_usage", lambda *a, **k: None)
    monkeypatch.setattr(cq, "_append_to_todays_slate", lambda t: None)
    monkeypatch.setattr(cq, "_log_sprint_tokens", lambda *a, **k: None)
    monkeypatch.setattr(cq, "_read_token_counts_from_log",
                        lambda tid: {"input_tokens": 0, "cache_write_tokens": 0,
                                     "cache_read_tokens": 0, "output_tokens": 0})
    monkeypatch.setattr(cq, "_with_status_prefix", lambda s, t: t)


def test_cmd_close_refuses_bare_proofless(store, monkeypatch):
    t = {"id": "T-bare-xyz", "status": "in_progress", "title": "x", "result": None}
    _stub_close_io(monkeypatch, t)
    with pytest.raises(SystemExit) as e:
        cq.cmd_close(["T-bare-xyz", "shipped it"])
    assert e.value.code == 1
    assert t["status"] == "in_progress"  # NOT closed


def test_cmd_close_shipped_unproven_flag_closes_and_flags(store, monkeypatch):
    t = {"id": "T-unproven-xyz", "status": "in_progress", "title": "x", "result": None}
    _stub_close_io(monkeypatch, t)
    cq.cmd_close(["T-unproven-xyz", "shipped it", "--shipped-unproven",
                  "no lever yet for this conceptual ticket"])
    assert t["status"] == "closed"
    assert t["proven"] is False
    assert "no lever yet" in t["unproven_reason"]
