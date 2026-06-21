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
The public ``prove()`` derives the pre-implementation tree from git (parent
commit in a throwaway worktree, with the test file overlaid from HEAD so a
test added in the same commit as its impl still exists in the red run). It does
NOT accept a red-state from the caller — see blocker #1 above. The injectable
``red_strategy`` seam on ``_run_proof`` exists ONLY for testing this harness and
is never exposed on the builder-facing API.

Bootstrap exception: this emitter cannot prove itself before it works. It closes
on its own pytest suite + the /sorted advisor review + Akien's inspection — NOT
on a self-emitted proof.

Scope (this ticket, T-proof-emitter-harness): gates expressible as a single
pytest test that goes red->green. Non-red->green intentions ("refactor changed
nothing", "robust under partial failure") are out of scope and close as
shipped-unproven until a later proof-kind covers them.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
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


@contextlib.contextmanager
def _git_parent_worktree(repo_root: str, test_nodeid: str,
                         parent_ref: str = "HEAD~1") -> Iterator[str]:
    """Materialize the pre-implementation tree as a detached worktree at
    ``parent_ref``, with the test file overlaid from the current HEAD so a test
    introduced in the same commit as its implementation still exists in the red
    run. Yields the worktree path to run the red pass in.
    """
    wt = tempfile.mkdtemp(prefix="proof_red_wt.")
    # mkdtemp created it; git worktree add needs the path absent.
    os.rmdir(wt)
    _git(repo_root, "worktree", "add", "--detach", wt, parent_ref)
    try:
        test_rel = test_nodeid.split("::", 1)[0]
        src = os.path.join(repo_root, test_rel)
        if os.path.exists(src):
            dst = os.path.join(wt, test_rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
        yield wt
    finally:
        _git(repo_root, "worktree", "remove", "--force", wt)


def _slug(text: str, n: int = 40) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (s[:n] or "thing").strip("-")


def _run_proof(*, thing: str, intention: str, test: str,
               ticket: Optional[str], narrative: str, why: str,
               red_strategy, commit: str, repo_root: str,
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
    commit = _git(repo_root, "rev-parse", "HEAD")
    strategy = _git_parent_worktree(repo_root, test, parent_ref)
    return _run_proof(thing=thing, intention=intention, test=test, ticket=ticket,
                      narrative=narrative, why=why, red_strategy=strategy,
                      commit=commit, repo_root=repo_root)


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
