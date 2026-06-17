#!/usr/bin/env python3
"""
cert_worker_freeze.py — T-flip-igor-worker-tickets-during-cert

# author-model: opus

Belt-and-suspenders for the cc-walk cert phase. The IGOR_SINGLE_TICKET env
var (T-igor-single-ticket-mode) is the primary kill-switch, but a
forgotten env var or fresh shell would let Igor's pe_chain start picking
worker=igor tickets again. This script flips every pending worker=igor
ticket to worker=claude pre-emptively (preserving the original worker in
metadata.original_worker for restoration after cert).

Igor's pe_chain filter at pe_chain.py:2704 already skips non-igor tickets,
so frozen tickets are invisible to his autonomous pickup loop. When cert
completes, --unfreeze restores worker=igor from metadata.original_worker.

Usage:
    cert_worker_freeze.py --freeze       # flip all pending worker=igor → claude
    cert_worker_freeze.py --unfreeze     # restore from metadata.original_worker
    cert_worker_freeze.py --status       # report frozen count + list

Idempotent: re-running --freeze on already-frozen tickets is a no-op.

TODO: remove after T-cc-walk-10 cert complete.
"""

from __future__ import annotations

import argparse
import json
import sys


def load() -> list[dict]:
    from devlab.claudecode import cc_queue

    return cc_queue.load_tasks()


def save(tasks: list[dict]) -> None:
    from devlab.claudecode import cc_queue

    cc_queue.save_tasks(tasks)


def freeze(tasks: list[dict]) -> tuple[int, list[str]]:
    """Flip pending worker=igor → claude. Returns (count, ids)."""
    flipped: list[str] = []
    for t in tasks:
        if t.get("status") != "pending":
            continue
        if t.get("worker") != "igor":
            continue
        meta = t.setdefault("metadata", {})
        if isinstance(meta, dict) and meta.get("original_worker") == "igor":
            # Already frozen earlier and somehow worker is still igor —
            # fix the worker but don't re-record original.
            t["worker"] = "claude"
            flipped.append(t["id"])
            continue
        if isinstance(meta, dict):
            meta["original_worker"] = "igor"
            meta["frozen_for_cert"] = True
        t["worker"] = "claude"
        flipped.append(t["id"])
    return len(flipped), flipped


def unfreeze(tasks: list[dict]) -> tuple[int, list[str]]:
    """Restore worker from metadata.original_worker. Returns (count, ids)."""
    restored: list[str] = []
    for t in tasks:
        meta = t.get("metadata") or {}
        if not isinstance(meta, dict):
            continue
        if not meta.get("frozen_for_cert"):
            continue
        original = meta.get("original_worker", "igor")
        t["worker"] = original
        meta.pop("frozen_for_cert", None)
        meta.pop("original_worker", None)
        restored.append(t["id"])
    return len(restored), restored


def status(tasks: list[dict]) -> tuple[int, list[str]]:
    """Count frozen tickets. Returns (count, ids)."""
    frozen: list[str] = []
    for t in tasks:
        meta = t.get("metadata") or {}
        if isinstance(meta, dict) and meta.get("frozen_for_cert"):
            frozen.append(t["id"])
    return len(frozen), frozen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--freeze", action="store_true", help="Flip pending worker=igor → claude"
    )
    group.add_argument(
        "--unfreeze", action="store_true", help="Restore from metadata.original_worker"
    )
    group.add_argument(
        "--status", action="store_true", help="Report frozen count + list"
    )
    parser.add_argument("--dry-run", action="store_true", help="Don't write changes")
    args = parser.parse_args(argv)

    tasks = load()

    if args.status:
        count, ids = status(tasks)
        print(f"frozen: {count}")
        for tid in ids:
            print(f"  {tid}")
        return 0

    if args.freeze:
        count, ids = freeze(tasks)
        action_word = "would freeze" if args.dry_run else "froze"
    else:
        count, ids = unfreeze(tasks)
        action_word = "would unfreeze" if args.dry_run else "unfroze"

    if not args.dry_run:
        save(tasks)
    print(f"{action_word}: {count}")
    for tid in ids:
        print(f"  {tid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
