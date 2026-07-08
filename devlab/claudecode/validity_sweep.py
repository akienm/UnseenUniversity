#!/usr/bin/env python3
"""validity_sweep — the pull side of the validity-conditions contract.

Every documented stale-memory incident (the IMAP transport claim, the dead
/sorted write-path, lab/ surviving as a target) shared one root cause: the entry
recorded nothing about what must remain true for it to hold, so nothing could flag
it when the world changed. Store entries now carry `validity_conditions`
(architecture/validity-conditions). This sweep runs at day-close, resolves each
stated condition against the CURRENT world, and FLAGS (annotates, never deletes —
CP2) entries whose condition no longer holds.

Three condition types (machine-first):
  depends-on-path      target is a repo-relative path (or `path::symbol`);
                       BREAKS when it no longer resolves at sweep time.
  depends-on-artifact  target is a store id (D-*/T-*/architecture); BREAKS when
                       that artifact is superseded/rejected/cancelled;
                       UNRESOLVABLE when the id resolves to no artifact (meta-rot).
  depends-on-fact      free-text with an OPTIONAL probe (grep pattern); BREAKS when
                       the probe finds no match; factless -> UNRESOLVABLE (weakest).

A broken condition appends `stale_flags: [{flagged_at, condition, reason,
sweep_run}]` to the entry's envelope in place; the entry stays readable. Clearing a
flag is a reviewed act (re-emit), never automatic. Meta-rot mitigation: path_moves
(rules/path_moves.json) is consulted before declaring a path gone.

Run: python3 devlab/claudecode/validity_sweep.py [--apply]
Prints `validity sweep: flagged=<N> checked=<M> unresolvable=<K>` — always, even
at all zeros. Without --apply it reports only (dry run); --apply writes the flags.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CLEAR, BROKEN, UNRESOLVABLE = "clear", "broken", "unresolvable"
_STALE_STATUS = ("superseded", "rejected", "cancelled")
_STORE_CATEGORIES = ("decisions", "tickets", "architecture", "rules", "notes", "proofs")


def _memory_root() -> str:
    return os.environ.get("UU_MEMORY_ROOT", os.path.join(_REPO, "devlab", "runtime", "memory"))


def _load_path_moves(memory_root: str) -> dict:
    """Map retired path prefix -> canonical prefix, from rules/path_moves.json."""
    p = os.path.join(memory_root, "rules", "path_moves.json")
    moves = {}
    try:
        data = json.load(open(p))
        for m in data.get("moves", []):
            if m.get("from") and m.get("to"):
                moves[m["from"]] = m["to"]
    except (OSError, ValueError, KeyError):
        pass
    return moves


def _follow_moves(path: str, moves: dict) -> str:
    for frm, to in moves.items():
        if path.startswith(frm):
            return to + path[len(frm):]
    return path


def build_store_index(memory_root: str) -> dict:
    """id/namespace -> body, across store categories, for artifact resolution."""
    index = {}
    for cat in _STORE_CATEGORIES:
        for fp in glob.glob(os.path.join(memory_root, cat, "**", "*.json"), recursive=True):
            try:
                rec = json.load(open(fp))
            except (OSError, ValueError):
                continue
            body = rec.get("body", {})
            for key in (body.get("id"), body.get("decision_id"),
                        body.get("subsystem"), rec.get("id")):
                if key:
                    index.setdefault(key, body)
    return index


def _grep_matches(pattern: str, repo: str) -> bool:
    """True if the probe pattern matches anywhere in tracked files. Fail-soft."""
    try:
        r = subprocess.run(["git", "-C", repo, "grep", "-lE", pattern],
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0 and bool(r.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def resolve_condition(cond: dict, *, repo: str, store_index: dict, moves: dict) -> tuple:
    """Return (state, reason). state in {CLEAR, BROKEN, UNRESOLVABLE}."""
    typ = cond.get("type")
    target = cond.get("target", "") or ""

    if typ == "depends-on-path":
        path, _, symbol = target.partition("::")
        resolved = _follow_moves(path, moves)
        full = os.path.join(repo, resolved)
        if not os.path.exists(full):
            return BROKEN, f"path {resolved!r} no longer resolves"
        if symbol:
            try:
                if symbol not in open(full, encoding="utf-8", errors="ignore").read():
                    return BROKEN, f"symbol {symbol!r} not found in {resolved}"
            except OSError:
                return BROKEN, f"cannot read {resolved} to check symbol"
        return CLEAR, ""

    if typ == "depends-on-artifact":
        body = store_index.get(target)
        if body is None:
            return UNRESOLVABLE, f"artifact {target!r} resolves to nothing (rename drift?)"
        status = str(body.get("status", "")).lower()
        if any(s in status for s in _STALE_STATUS):
            return BROKEN, f"artifact {target!r} is {status!r}"
        return CLEAR, ""

    if typ == "depends-on-fact":
        probe = cond.get("probe")
        if not probe:
            return UNRESOLVABLE, "fact has no probe — not machine-checkable"
        return (CLEAR, "") if _grep_matches(probe, repo) else \
            (BROKEN, f"probe {probe!r} found no match")

    return UNRESOLVABLE, f"unknown condition type {typ!r}"


def _iter_entries(memory_root: str):
    """Yield (filepath, record) for every store JSON carrying validity_conditions."""
    for cat in _STORE_CATEGORIES:
        for fp in glob.glob(os.path.join(memory_root, cat, "**", "*.json"), recursive=True):
            try:
                rec = json.load(open(fp))
            except (OSError, ValueError):
                continue
            if rec.get("validity_conditions"):
                yield fp, rec


def sweep(memory_root: str | None = None, *, repo: str | None = None,
          apply: bool = False, sweep_run: str = "manual") -> dict:
    """Resolve conditions across the store. Returns a summary dict; annotates
    flagged entries in place when apply=True. Never deletes."""
    memory_root = memory_root or _memory_root()
    repo = repo or _REPO
    moves = _load_path_moves(memory_root)
    store_index = build_store_index(memory_root)

    checked = flagged = unresolvable = 0
    flagged_entries, curate_candidates = [], []

    for fp, rec in _iter_entries(memory_root):
        new_flags = []
        for cond in rec["validity_conditions"]:
            checked += 1
            state, reason = resolve_condition(
                cond, repo=repo, store_index=store_index, moves=moves)
            if state == UNRESOLVABLE:
                unresolvable += 1
            if state in (BROKEN, UNRESOLVABLE):
                new_flags.append({
                    "flagged_at": sweep_run,
                    "condition": cond,
                    "reason": reason,
                    "state": state,
                    "sweep_run": sweep_run,
                })
        if new_flags:
            flagged += 1
            eid = rec.get("body", {}).get("id") or rec.get("id")
            flagged_entries.append({"id": eid, "path": fp, "flags": new_flags})
            existing = rec.get("stale_flags", [])
            # >=2 distinct sweeps flagging this entry -> CURATE candidate (concept 4)
            prior_runs = {f.get("sweep_run") for f in existing}
            if prior_runs and sweep_run not in prior_runs:
                curate_candidates.append(eid)
            if apply:
                rec["stale_flags"] = existing + new_flags
                tmp = fp + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(rec, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                os.replace(tmp, fp)

    return {
        "checked": checked,
        "flagged": flagged,
        "unresolvable": unresolvable,
        "flagged_entries": flagged_entries,
        "curate_candidates": curate_candidates,
    }


def format_summary(result: dict) -> str:
    return (f"validity sweep: flagged={result['flagged']} "
            f"checked={result['checked']} unresolvable={result['unresolvable']}")


def main() -> int:
    apply = "--apply" in sys.argv[1:]
    result = sweep(apply=apply, sweep_run="daily" if apply else "dryrun")
    print(format_summary(result))
    for e in result["flagged_entries"]:
        for fl in e["flags"]:
            mark = "⚠ CURATE" if e["id"] in result["curate_candidates"] else fl["state"]
            print(f"  [{mark}] {e['id']}: {fl['reason']}")
    if not apply and result["flagged"]:
        print("  (dry run — re-run with --apply to write stale_flags)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
