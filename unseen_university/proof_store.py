"""Filesystem proof-store reader + validity check — the close-gate's eyes.

Companion to ``ticket_store.py`` / ``memory_emit.py``: proofs are emitted by
``devlab/claudecode/proof_emitter.py`` into ``<store>/proofs/*.json`` with the
shared envelope (``id/emitter/namespace/category/kind/emitted_at/links/body``).
This module is the READ side the close-gate needs — find the proof(s) for a
ticket and decide whether a proof still binds to the current HEAD.

Validity (D-proof-on-close-2026-06-20, "Close-gate RESOLVED + CP1-6 review"):
a proof is VALID for closing ticket T iff
  1. ``links.tickets`` contains T's (bare) id, AND
  2. ``body.commit`` is reachable from HEAD (an ancestor, or HEAD itself), AND
  3. there is NO drift in the recorded ``body.impl_paths``
     (``git diff <commit> HEAD -- <impl_paths>`` is empty).

Why commit-reachable rather than ``== HEAD``: after prove() emits the proof file
(untracked), it gets committed in a *later* commit, so HEAD moves ahead of the
proof's commit. That later commit adds only the proof file — it does not touch
``impl_paths`` — so reachability + impl-path-scoped drift together still catch a
hollow drift (someone edited the implementation after proving) while permitting
the proof-commit itself. A proof with no recorded ``impl_paths`` cannot have its
drift scoped, so it is INVALID — re-prove with an emitter that records them.

Design rules honoured:
- **NO SQLITE / NO POSTGRES.** Pure filesystem read + git subprocess.
- Reads are lock-free (atomic-replace files are always valid).
- Same ``UU_MEMORY_ROOT`` convention as ticket_store, so tests can redirect it.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from unseen_university._uu_root import uu_root

log = logging.getLogger(__name__)


def _memory_root() -> Path:
    val = os.environ.get("UU_MEMORY_ROOT")
    if val:
        return Path(val)
    return Path(uu_root()) / "devlab" / "runtime" / "memory"


def _proofs_dir() -> Path:
    return _memory_root() / "proofs"


def _iter_proofs():
    d = _proofs_dir()
    if not d.exists():
        return
    for p in sorted(d.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # unreadable file — skip, never crash a read
            log.warning("proof_store: unreadable file %s: %s", p.name, exc)
            continue
        if isinstance(rec, dict) and isinstance(rec.get("body"), dict):
            yield p, rec


def find_for_ticket(ticket_id: str) -> "list[dict]":
    """Return proof envelopes whose ``links.tickets`` contains ``ticket_id``.

    Matches on the bare semantic id (e.g. ``T-foo``) — the same id cc_queue's
    ``body.id`` carries and ``proof_emitter`` writes into ``links.tickets`` — so
    the lookup does not silently miss against the namespaced filename id.
    """
    out = []
    for _, rec in _iter_proofs():
        tickets = (rec.get("links") or {}).get("tickets") or []
        if ticket_id in tickets:
            out.append(rec)
    return out


def _git(repo_root: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo_root, *args],
        capture_output=True, text=True,
    )


def validate(proof: dict, repo_root: str) -> "tuple[bool, str]":
    """Return ``(is_valid, reason)`` for a single proof against HEAD.

    ``reason`` is empty on success, else a human-readable rejection cause.
    """
    body = proof.get("body") or {}
    commit = body.get("commit") or ((proof.get("links") or {}).get("commits") or [None])[0]
    if not commit:
        return False, "proof records no commit — cannot bind it to HEAD"

    # 1. reachability: commit must be an ancestor of (or equal to) HEAD.
    anc = _git(repo_root, "merge-base", "--is-ancestor", commit, "HEAD")
    if anc.returncode != 0:
        # rc 1 = not an ancestor; rc 128 = unknown/invalid commit.
        if anc.returncode == 128:
            return False, f"proof commit {commit[:12]} not found in this repo"
        return False, (
            f"proof commit {commit[:12]} is not reachable from HEAD — the proof "
            "predates the current branch tip (stale / orphaned)"
        )

    # 2. drift: impl_paths must be recorded AND unchanged between commit and HEAD.
    impl_paths = body.get("impl_paths")
    if not impl_paths:
        return False, (
            "proof records no impl_paths — drift cannot be scoped, so the proof "
            "cannot be validated. Re-prove with the current emitter."
        )
    diff = _git(repo_root, "diff", "--quiet", commit, "HEAD", "--", *impl_paths)
    if diff.returncode == 1:
        changed = _git(repo_root, "diff", "--name-only", commit, "HEAD", "--", *impl_paths)
        return False, (
            "implementation drifted since the proof was emitted — these paths "
            f"changed between {commit[:12]} and HEAD:\n{changed.stdout.strip()}\n"
            "The proof no longer covers the current code. Re-prove."
        )
    if diff.returncode not in (0, 1):
        return False, f"git diff failed while checking drift (rc={diff.returncode}): {diff.stderr.strip()}"

    return True, ""


def best_valid_proof(ticket_id: str, repo_root: str) -> "tuple[Optional[dict], list[str]]":
    """Return ``(proof_or_None, rejections)`` — the first valid proof for the
    ticket, plus the reasons any candidates were rejected (for the operator).
    """
    rejections = []
    proofs = find_for_ticket(ticket_id)
    if not proofs:
        return None, [f"no proof emitted for {ticket_id}"]
    for rec in proofs:
        ok, reason = validate(rec, repo_root)
        if ok:
            return rec, rejections
        rejections.append(f"{rec.get('id', '?')}: {reason}")
    return None, rejections
