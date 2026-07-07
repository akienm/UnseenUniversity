#!/usr/bin/env python3
"""builder_merge_proof — the merge-time half of branch-builder proof-on-close.

A branch-builder (Aider, DickSimnel) closes shipped-unproven: its implementation
lives on an unmerged branch in a throwaway clone, so it CANNOT emit a HEAD-valid
proof at build time (see unseen_university/devices/_builder_close.BuilderCloseMixin).
That is honest but incomplete — an unvalidated shipped-unproven close just shifts
eyeball-validation onto CC. This module is the OTHER half: after CC validates and
merges the builder branch to HEAD (so the impl is now AT HEAD, parent_ref = the
pre-merge commit), `emit_merge_proof` emits a red->green proof against the ticket's
test. The proof binds to the merge HEAD, so proof_store.best_valid_proof now finds it
and the proof-on-close gate flips the ticket shipped-unproven -> proven on the next
close. That flip is what makes builder throughput actually reduce CC's queue instead
of shifting it (T-builder-merge-time-proof).

TRIGGER: CC's merge/validation step calls this. There is intentionally NO merger
device / watcher (scope boundary of the ticket) — this is the mechanism that step
invokes, not a daemon.

RED-form note (inherited from proof_emitter._git_inplace_red): the merge-red inverts
the parent_ref..HEAD impl delta while keeping the test. It is authentic when the
branch MODIFIED existing files; a branch that only ADDED a new impl file yields a
collateral ImportError red -> proof rejected with stub-first guidance, exactly as at
build time. Honest either way: a rejected proof leaves the ticket shipped-unproven.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

# Bootstrap this dir so the sibling script-modules (proof_emitter -> memory_emit)
# resolve whether we're imported as devlab.claudecode.builder_merge_proof or run as a
# script. Same pattern as cc_queue.py.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import proof_emitter  # noqa: E402  (sibling script-module)
from unseen_university import proof_store  # noqa: E402


def _toplevel(cwd: str | None = None) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=cwd or os.getcwd(), capture_output=True, text=True,
    )
    return r.stdout.strip()


def emit_merge_proof(ticket_id: str, test_node: str, *, thing: str, intention: str,
                     parent_ref: str, repo_root: str | None = None, why: str = "") -> dict:
    """Emit a merge-time red->green proof for a just-merged builder branch and report
    whether the ticket's close-gate will now flip to proven.

    parent_ref is the PRE-MERGE commit (green = merged HEAD, red = parent_ref). This
    function is pure with respect to the ticket queue — the caller (CC's merge step,
    or the CLI --flip) runs `cc_queue close` to apply the flip. Returns
    {proof_id, valid, rejections}; valid=True means a re-close (no --shipped-unproven)
    will now pass the gate and set proven=True.
    """
    repo_root = repo_root or _toplevel()
    proof_emitter.prove(
        thing, intention, test_node, ticket=ticket_id, why=why,
        repo_root=repo_root, parent_ref=parent_ref,
    )
    # Source the id from the stored, gate-validated proof envelope (not the prove()
    # summary) — it's the proof the close-gate will actually bind to.
    proof, rejections = proof_store.best_valid_proof(ticket_id, repo_root)
    return {
        "proof_id": (proof or {}).get("id"),
        "valid": proof is not None,
        "rejections": rejections,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Emit the merge-time proof that flips a "
                                             "branch-builder ticket to proven")
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--test", required=True, help="pytest node id, e.g. tests/test_x.py::test_y")
    ap.add_argument("--thing", required=True)
    ap.add_argument("--intention", required=True)
    ap.add_argument("--parent-ref", required=True, help="the PRE-MERGE commit ref")
    ap.add_argument("--why", default="")
    ap.add_argument("--flip", action="store_true",
                    help="on a valid proof: commit it + re-close the ticket (no "
                         "--shipped-unproven) so the gate flips proven=True")
    args = ap.parse_args()

    res = emit_merge_proof(args.ticket, args.test, thing=args.thing,
                           intention=args.intention, parent_ref=args.parent_ref, why=args.why)
    print(f"proof={res['proof_id']} valid={res['valid']} rejections={res['rejections']}")

    if args.flip and res["valid"]:
        # Commit the emitted (untracked) proof file so it is HEAD-reachable, then
        # re-close: the gate re-evaluates and flips proven False->True.
        subprocess.run(["git", "add", "devlab/runtime/memory/proofs"], cwd=_toplevel())
        subprocess.run(["git", "commit", "-q", "-m", f"proof(merge): {args.ticket}"], cwd=_toplevel())
        cc_queue = os.path.join(_HERE, "cc_queue.py")
        subprocess.run([sys.executable, cc_queue, "close", args.ticket,
                        f"merged + validated; merge-time proof {res['proof_id']}"])
    sys.exit(0 if res["valid"] else 1)


if __name__ == "__main__":
    main()
