"""Proof emitter — the spine of proof-on-close (D-proof-on-close-2026-06-20, step 1b).

A *proof* is a JSON artifact a hollow implementation could not have produced,
emitted as a byproduct of the harness running a gate. "Done" stops being a
builder claim and becomes a discharged burden: a ticket closes only by pointing
at a proof whose commit matches HEAD (enforced later by T-ticket-close-requires-proof).

WHAT THIS HARNESS DOES (and why each step is load-bearing)
----------------------------------------------------------
Given one falsifiable intention operationalized as one pytest test, the harness:

  1. Runs the test against the CURRENT (implemented) tree  -> must PASS (green).
  2. Runs the test against the PRE-IMPLEMENTATION tree     -> must FAIL (red),
     and the failure must be an *assertion about behavior*, not a collateral
     error (ImportError / NameError / collection error).
  3. Emits a proof recording BOTH harness-generated runs, bound to HEAD.

The harness GENERATES and RUNS both passes itself (subprocess pytest); it never
accepts a builder-supplied "red" result. That is the whole point: if a builder
could hand in red, a builder could fabricate red, and we would ship a hollow
build of the anti-hollow-build machinery.

THE STUB-FIRST CONVENTION (a deliberate trade — flagged for review)
-------------------------------------------------------------------
Authentic red == AssertionError or pytest Failed only. Red produced by a missing
symbol or import (NameError/AttributeError/ImportError/collection error) is
REJECTED as collateral. Rationale, verified empirically (see test suite): if red
comes from "the symbol doesn't exist yet", then green merely means "the symbol
now exists" — which a hollow stub satisfies, so the test proves nothing. Red
that comes from an *assertion about a value/behavior* means green proves the
behavior is actually correct; a hollow stub fails that assert.

Consequence: to prove a thing, write a stub so symbols/imports resolve, and let
the test fail on an assertion about behavior. This is intentional strictness for
an anti-hollow gate. Akien may choose to broaden it at review; it is isolated in
``AUTHENTIC_RED_EXC`` for exactly that reason.

PRODUCTION RED IS DERIVED FROM GIT, NOT THE CALLER
--------------------------------------------------
The public ``prove()`` derives the pre-implementation state from git by inverting
the parent..HEAD diff IN PLACE (see ``_git_inplace_red``) — never from a
caller-supplied red-state. In-place is required because UU is an editable install
whose PEP 660 finder resolves imports to the working tree; a separate worktree
would be shadowed by the installed copy. The injectable ``red_strategy`` seam on
``_run_proof`` exists ONLY for testing this harness and is never exposed on the
builder-facing API.

Bootstrap exception: this emitter cannot prove itself before it works. It closes
on its own pytest suite + the /sorted advisor review + Akien's inspection — NOT
on a self-emitted proof.

Scope (this ticket, T-proof-emitter-harness): gates expressible as a single
pytest test that goes red->green. Non-red->green intentions ("refactor changed
nothing", "robust under partial failure") are out of scope and close as
shipped-unproven until a later proof-kind covers them.

KNOWN BEHAVIOURS / LIMITATIONS (be honest about these)
------------------------------------------------------
- SHARED-TREE MUTATION: in-place red mutates the real working tree for the red
  window, so any live process importing the package momentarily sees parent_ref
  code. Small window, low risk, but a real isolation trade vs a worktree (which
  we can't use here — the editable finder shadows it). Clean-tree precondition +
  restore-must-raise bound the blast radius.
- WEAK RED FOR MULTI-COMMIT THINGS: red = HEAD~1 removes only the last commit.
  Under "commit each bug-fix cycle", a single thing can span commits, so HEAD~1
  is "impl minus last tweak", not full pre-implementation. Sound for single-commit
  things; weaker otherwise. (Future: a proof could span thing-start..HEAD.)
- ADDED IMPL FILES need stub-first: a file ADDED in HEAD is removed for the red
  run, so a test importing it gets ImportError → collateral → correctly rejected
  with stub-first guidance. Prove new work as stub-commit then impl-commit (makes
  the impl file an M, whose stub→real change yields authentic assertion red).
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Iterator, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import memory_emit  # noqa: E402  (sibling module, canonical emission writer)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))

# Failure exception classes that count as an authentic assertion-about-behavior
# red. Everything else (NameError, AttributeError, ImportError, ...) is a
# collateral error and is rejected. See "stub-first convention" above.
AUTHENTIC_RED_EXC = {"AssertionError", "Failed"}


class ProofError(Exception):
    """Raised when a proof cannot be honestly emitted (green didn't pass, red
    wasn't authentic, red came back green, the test wasn't found, or the harness
    itself failed to run). A ProofError means: no proof — never a silent pass."""


@dataclass
class ProofRun:
    """One harness-generated pytest run of a single test node."""
    nodeid: str
    outcome: str              # "passed" | "failed" | "error"
    exc_type: Optional[str]   # exception class name on failure, else None
    exit_code: int
    summary: str              # trimmed stdout tail, for evidence/debugging

    def as_evidence(self) -> dict:
        return {
            "outcome": self.outcome,
            "exc_type": self.exc_type,
            "exit_code": self.exit_code,
            "summary": self.summary,
        }


def _func_tail(nodeid: str) -> str:
    """The ``file_basename::...::func`` tail, for matching reports across cwds."""
    if "::" not in nodeid:
        return os.path.basename(nodeid)
    path, rest = nodeid.split("::", 1)
    return os.path.basename(path) + "::" + rest


def _run_pytest(nodeid: str, *, cwd: str, timeout: int = 180) -> ProofRun:
    """Run ONE pytest node in a subprocess and classify its single call report.

    ``cwd`` is prepended to PYTHONPATH so the code under ``cwd`` shadows any
    pip-installed copy of the same package — essential for running the red pass
    against a git worktree's (older) code rather than the installed tree.
    """
    fd, out_path = tempfile.mkstemp(suffix=".proofrun.json")
    os.close(fd)
    env = dict(os.environ)
    env["PROOF_RUN_OUT"] = out_path
    env["PYTHONPATH"] = os.pathsep.join(
        [cwd, _THIS_DIR] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])
    )
    cmd = [
        sys.executable, "-m", "pytest", nodeid,
        "-p", "no:cacheprovider", "-p", "_proof_pytest_plugin",
        "-q", "--no-header",
    ]
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                              text=True, timeout=timeout)
        try:
            with open(out_path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            raise ProofError(
                f"harness could not run the test (no plugin output): {nodeid} "
                f"[exit={proc.returncode}]\n{proc.stdout[-2000:]}\n{proc.stderr[-1000:]}"
            ) from e
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(out_path)

    summary = "\n".join(proc.stdout.strip().splitlines()[-20:])
    reports = data.get("reports", [])
    if data.get("collect_errors"):
        return ProofRun(nodeid, "error", "CollectionError", data.get("exit", -1), summary)
    # Match the target test's call report (single-node runs usually yield one).
    tail = _func_tail(nodeid)
    match = None
    if len(reports) == 1:
        match = reports[0]
    else:
        for r in reports:
            if r["nodeid"].endswith(tail) or _func_tail(r["nodeid"]) == tail:
                match = r
                break
    if match is None:
        return ProofRun(nodeid, "error", "NoTestCollected", data.get("exit", -1), summary)
    return ProofRun(nodeid, match["outcome"], match.get("exc_type"),
                   data.get("exit", -1), summary)


def is_authentic_red(run: ProofRun) -> bool:
    """True iff the run failed on an assertion about behavior (not collateral)."""
    return run.outcome == "failed" and run.exc_type in AUTHENTIC_RED_EXC


# ---------------------------------------------------------------------------
# Red strategies. The git strategy is production. The seam is internal — the
# public prove() never lets a caller supply one (blocker #1).
# ---------------------------------------------------------------------------

def _git(repo_root: str, *args: str) -> str:
    proc = subprocess.run(["git", "-C", repo_root, *args],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        raise ProofError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _impl_changes(repo_root: str, test_file: str, parent_ref: str):
    """Per-file status of the ``parent_ref``..HEAD diff, EXCLUDING the test file
    (the test stays at its current/HEAD intention for the red run). Returns a
    list of (status, path) with status in {M, A, D}; renames are split into A+D.
    """
    raw = _git(repo_root, "diff", "--name-status", parent_ref, "HEAD")
    changes = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        code = parts[0][0]
        if code == "R" and len(parts) >= 3:          # rename = del(old) + add(new)
            old, new = parts[1], parts[2]
            if new != test_file:
                changes.append(("A", new))
            if old != test_file:
                changes.append(("D", old))
            continue
        path = parts[1]
        if path == test_file:
            continue
        changes.append((code, path))
    return changes


@contextlib.contextmanager
def _git_inplace_red(repo_root: str, test_nodeid: str,
                     parent_ref: str = "HEAD~1") -> Iterator[str]:
    """Materialize the pre-implementation state IN PLACE by inverting the
    ``parent_ref``..HEAD diff per file status, run red in repo_root, then restore.

    Works WITH the editable install: UU's PEP 660 finder resolves imports to the
    working tree, so changing files in place changes what gets imported — which
    the worktree+PYTHONPATH approach could not do (the finder beat PYTHONPATH).
    SAFE only because prove() requires a clean tree first: every file we touch is
    restorable from a commit, with nothing uncommitted to clobber.

    Per-status inversion (the added-file case is the one a naive
    `git checkout parent -- path` silently breaks — it errors on a path absent in
    parent and leaves HEAD's file in place, yielding a false "vacuous" rejection):
      - M (modified): checkout parent_ref version for red; checkout HEAD to restore.
      - A (added in HEAD): remove the file for red; checkout HEAD to restore.
        (A typical added *impl* file → test ImportError → collateral → the proof
        is correctly rejected with stub-first guidance. The way to prove new work
        is stub-commit-then-impl-commit, which makes the impl file an M, not an A.)
      - D (deleted in HEAD): resurrect parent_ref version for red; git rm to restore.

    Caveats (documented, not solved here):
      - In-place mutates the SHARED working tree: any live process importing the
        package sees parent_ref code for the red window. Small, but a real
        isolation regression vs the (broken-for-this) worktree approach.
      - red = parent_ref (HEAD~1) removes only the LAST commit. With the "commit
        each bug-fix cycle" discipline a single *thing* can span several commits,
        so HEAD~1 is "impl minus last tweak", not full pre-implementation — a
        weaker red than it looks. Fine for single-commit things; weaker otherwise.
    """
    test_file = test_nodeid.split("::", 1)[0]
    changes = _impl_changes(repo_root, test_file, parent_ref)
    if not changes:
        raise ProofError(
            f"no implementation delta between {parent_ref} and HEAD (only the "
            f"test changed?) — cannot generate authentic red. The thing's "
            f"implementation must be part of this commit."
        )

    def _to_red():
        for code, path in changes:
            if code in ("M", "D"):                    # revert / resurrect parent
                _git(repo_root, "checkout", parent_ref, "--", path)
            elif code == "A":                          # remove HEAD-added file
                fp = os.path.join(repo_root, path)
                if os.path.exists(fp):
                    os.remove(fp)

    def _restore():
        # Idempotent: must fully restore even if _to_red threw partway (so some
        # paths were never mutated). M/A checkouts are no-ops when already at
        # HEAD; --ignore-unmatch makes the D removal a no-op when the file was
        # never resurrected.
        for code, path in changes:
            if code in ("M", "A"):
                _git(repo_root, "checkout", "HEAD", "--", path)
            elif code == "D":                          # must not exist at HEAD
                _git(repo_root, "rm", "-f", "--ignore-unmatch", "--quiet", "--", path)

    # _to_red MUST be inside the try so a partial-mutation failure still restores
    # — a half-mutated live tree with no auto-recovery is the exact hidden state
    # the clean-tree discipline exists to prevent.
    try:
        _to_red()
        yield repo_root
    finally:
        _restore()
        # Restore must be PERFECT. Leftover state is exactly the hidden state the
        # no-stash principle bans → RAISE, don't warn (halt-until-sorted). The
        # next prove()'s clean-tree guard is the backstop, but we fail loud here.
        # --untracked-files=no: the red mechanism only mutates TRACKED files, so
        # we verify those are restored; untracked cruft (pytest __pycache__, etc.)
        # is not something restore created or should police.
        dirty = _git(repo_root, "status", "--porcelain", "--untracked-files=no")
        if dirty:
            raise ProofError(
                "red-state restore FAILED — working tree not clean after the "
                f"proof run. Leftover:\n{dirty}\nSort it immediately "
                "(git checkout / git clean) before any further work."
            )


def _slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:n] or "thing").strip("-")


def _run_proof(*, thing: str, intention: str, test: str,
               ticket: Optional[str], narrative: str, why: str,
               red_strategy, commit: str, repo_root: str,
               impl_paths: Optional[list] = None,
               emitter: str = "cc.0") -> dict:
    """Core: green at HEAD, authenticated red via ``red_strategy``, then emit.

    ``red_strategy`` is a context manager yielding the cwd to run the red pass
    in. Internal/test-only seam — see module docstring.
    """
    green = _run_pytest(test, cwd=repo_root)
    if green.outcome != "passed":
        raise ProofError(
            f"green run did not pass at HEAD: {test} -> {green.outcome} "
            f"({green.exc_type}). A proof requires the test to pass on the "
            f"implemented tree.\n{green.summary}"
        )

    with red_strategy as red_cwd:
        red = _run_pytest(test, cwd=red_cwd)

    if red.outcome == "passed":
        raise ProofError(
            "could not generate authentic red — the test passes in the "
            "pre-implementation state. Either the implementation is already "
            "present in that state, or the test is vacuous (does not exercise "
            "the intention)."
        )
    if not is_authentic_red(red):
        raise ProofError(
            f"red is a collateral error ({red.exc_type}), not an assertion about "
            f"behavior. proof-on-close requires the stub-first convention: write "
            f"a stub so symbols/imports resolve, and let the test fail on an "
            f"assertion about the value/behavior the intention claims.\n{red.summary}"
        )

    body = {
        "thing": thing,
        "intention": intention,
        "test": test,
        "kind_detail": "red-green",
        "gates": [{
            "name": test,
            "result": "green",
            "evidence": {
                "red_run": {**red.as_evidence(), "authentic_red": True},
                "green_run": green.as_evidence(),
            },
        }],
        "commit": commit,          # mirrored; canonical home is links.commits
        # impl_paths: the implementation files this proof binds to (the
        # parent_ref..HEAD delta minus the test). The close-gate's drift check
        # (`git diff proof.commit HEAD -- <impl_paths>`) reads exactly this —
        # without it, drift can't be scoped and a proof can't be validated.
        "impl_paths": sorted(impl_paths or []),
        "ticket": ticket,
        "narrative": narrative,
        "why": why,
        "bootstrap": False,
    }
    links = {"commits": [commit]}
    if ticket:
        links["tickets"] = [ticket]
    namespace = [_slug(thing)] + ([ticket] if ticket else [])
    path = memory_emit.emit("proofs", emitter, body, kind="proof",
                            namespace=namespace, links=links)
    return {"path": path, "commit": commit, "red": red.as_evidence(),
            "green": green.as_evidence()}


def prove(thing: str, intention: str, test: str, *,
          ticket: Optional[str] = None, narrative: str = "", why: str = "",
          repo_root: Optional[str] = None, parent_ref: str = "HEAD~1") -> dict:
    """Public entry: prove a thing by authenticating red->green, derive the
    pre-implementation tree from git (NOT from the caller), emit a HEAD-bound
    proof. Returns the emitted record summary. Raises ProofError if no honest
    proof can be produced.

    The implementation must be committed at HEAD (so commit-binding holds and
    the parent ref is genuinely pre-implementation).
    """
    repo_root = repo_root or _git(os.getcwd(), "rev-parse", "--show-toplevel")
    # Clean-tree invariant: the proof binds to HEAD, but green runs against the
    # working tree. A dirty tree would emit a proof claiming "passed at HEAD"
    # when it passed with uncommitted code — defeating commit-bound drift
    # detection. Enforce, don't assume.
    if _git(repo_root, "status", "--porcelain"):
        raise ProofError(
            "working tree is dirty — a proof binds to HEAD but green would run "
            "against uncommitted changes (commit-binding would lie). Commit or "
            "stash first, then prove."
        )
    commit = _git(repo_root, "rev-parse", "HEAD")
    # Impl paths the proof binds to (same computation _git_inplace_red uses, so
    # they agree). Recorded in the body for the close-gate's drift check.
    test_file = test.split("::", 1)[0]
    impl_paths = sorted({path for _, path in _impl_changes(repo_root, test_file, parent_ref)})
    strategy = _git_inplace_red(repo_root, test, parent_ref)
    return _run_proof(thing=thing, intention=intention, test=test, ticket=ticket,
                      narrative=narrative, why=why, red_strategy=strategy,
                      commit=commit, repo_root=repo_root, impl_paths=impl_paths)


def _main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Emit a proof for a thing (proof-on-close).")
    p.add_argument("--thing", required=True, help="what is being proven")
    p.add_argument("--intention", required=True, help="the one falsifiable intention")
    p.add_argument("--test", required=True, help="pytest node id, e.g. tests/test_x.py::test_y")
    p.add_argument("--ticket", default=None)
    p.add_argument("--narrative", default="")
    p.add_argument("--why", default="")
    p.add_argument("--parent-ref", default="HEAD~1")
    args = p.parse_args(argv)
    try:
        rec = prove(args.thing, args.intention, args.test, ticket=args.ticket,
                    narrative=args.narrative, why=args.why, parent_ref=args.parent_ref)
    except ProofError as e:
        print(f"NO PROOF: {e}", file=sys.stderr)
        return 1
    print(f"PROOF EMITTED: {rec['path']}\n  commit={rec['commit']}\n"
          f"  red={rec['red']['exc_type']} green=passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
