#!/usr/bin/env python3
"""intention_emit — the write side of the INTENTION artifact (proof-on-write gate).

Companion to ``design_emit.py`` / ``decision_manager.py``: the CC-side writer that
turns an intention body into a stored artifact. The READ side + the contract live
in ``unseen_university/intention_store.py``.

``validate_intention`` is enforced HERE, in code, before any write — a soft
"please validate" line in a skill is attacker-controlled (audit-smell-evasion
pattern), so a hollow intention (a deconstruction whose sub-intentions carry no
proof-obligation) must be *unable* to reach the store.

Unlike ``design_emit`` there is NO projection: ``intentions`` is already the
canonical category with no legacy reader-gap to bridge.

NO SQLITE / NO POSTGRES — pure filesystem via the memory_emit chokepoint.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys

# devlab/claudecode/intention_emit.py -> repo root is three dirs up.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from devlab.claudecode import memory_emit  # noqa: E402
from unseen_university.intention_store import validate_intention  # noqa: E402

log = logging.getLogger(__name__)


def emit_intention(body: dict, *, emitter: str = "cc.0", links: dict | None = None,
                   stamp: str | None = None, produced_by: str | None = None,
                   deconstructed: bool = False) -> str:
    """Validate + emit an intention. Returns the path written.

    Raises ``IntentionValidationError`` (from ``validate_intention``) BEFORE any
    write when the body is hollow — nothing lands in the store on a rejected
    intention. ``deconstructed=True`` validates against the full tier (sub-intentions
    each carrying a proof-obligation).
    """
    validate_intention(body, deconstructed=deconstructed)  # non-evadable gate

    intention_id = body["intention_id"]
    path = memory_emit.emit(
        "intentions", emitter, body, kind="intention",
        namespace=[intention_id], links=links, stamp=stamp,
        produced_by=produced_by,
    )
    # Log the store crossing (state change + interface crossing — AR-009).
    n_sub = len(body.get("sub_intentions") or []) if deconstructed else 0
    log.info("intention_emit: %s %s written%s", "deconstructed" if deconstructed
             else "captured", intention_id,
             f" ({n_sub} sub-intentions)" if deconstructed else "")
    return path


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Emit an INTENTION artifact (validated).")
    ap.add_argument("--emitter", default="cc.0")
    ap.add_argument("--stamp", default=None,
                    help="yyyymmdd.hhmmssuuuuuu (reuse to overwrite in place)")
    ap.add_argument("--links", default=None, help="JSON dict of semantic-id links")
    ap.add_argument("--produced-by", default=None,
                    help="backward edge: what produced this intention (human:akien, session:cc.0)")
    ap.add_argument("--deconstructed", action="store_true",
                    help="validate the full tier: sub-intentions each with a proof-obligation")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--body", help="inline JSON intention body")
    g.add_argument("--body-file", help="path to a JSON intention body file")
    args = ap.parse_args(argv)

    if args.body_file:
        with open(args.body_file) as f:
            body = json.load(f)
    elif args.body:
        body = json.loads(args.body)
    else:
        body = json.load(sys.stdin)

    links = json.loads(args.links) if args.links else None
    path = emit_intention(body, emitter=args.emitter, links=links, stamp=args.stamp,
                          produced_by=args.produced_by, deconstructed=args.deconstructed)
    print(path)


if __name__ == "__main__":
    _main()
