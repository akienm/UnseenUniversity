#!/usr/bin/env python3
"""design_emit — the write side of the DESIGN artifact (proof-on-write gate).

Companion to ``decision_manager.py`` (decisions) and ``proof_emitter.py``
(proofs): the CC-side writer that turns a design body into a stored artifact.
The READ side + the contract live in ``unseen_university/design_store.py``.

Two things happen atomically-in-intent on every emit:

  1. **The design lands in ``designs/``** via ``memory_emit`` — but ONLY after
     ``validate_design`` passes. Validation is enforced HERE, in code, not as a
     skill-prose "please run the validator" line: a soft gate is
     attacker-controlled (audit-smell-evasion pattern), so a hollow, fork-less
     design must be *unable* to reach the store.

  2. **Decision projection is now a READ-model, not a write.** ``emit_design``
     defaults ``project_decision=False``: the decision-shaped view is projected
     ON READ by ``design_store.iter_decision_view`` (the field-map lives in
     ``design_store.project_decision_body`` — ONE definition), so no back-compat
     ``D-*`` file is materialised into ``decisions/`` (CLAUDE.md "one home"). The
     ``project_decision=True`` path is retained ONLY for the rare case a caller
     wants a materialised projection; it field-maps via the same shared helper.
     (The materialised projection was the transitional bridge while the readers
     still globbed ``decisions/``; T-migrate-decision-readers-to-designs pointed
     them at ``iter_decision_view`` and retired the default write.)

NO SQLITE / NO POSTGRES — pure filesystem via the memory_emit chokepoint.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys

log = logging.getLogger(__name__)

# devlab/claudecode/design_emit.py -> repo root is three dirs up. Make the
# package importable without requiring an editable install to be active.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from devlab.claudecode import memory_emit  # noqa: E402
from unseen_university.design_store import (  # noqa: E402
    validate_design,
    decision_id_from_design as _decision_id_from_design,
    project_decision_body,
)


def emit_design(body: dict, *, emitter: str = "cc.0", links: dict | None = None,
                stamp: str | None = None, produced_by: str | None = None,
                project_decision: bool = False, draft: bool = False) -> dict:
    """Validate + emit a design, projecting a back-compat decision by default.

    Returns ``{"design_path": ..., "decision_path": ... | None}``.
    Raises ``DesignValidationError`` (from ``validate_design``) BEFORE any write
    when the body is hollow — nothing lands in the store on a rejected design.

    ``draft=True`` (the ``/design`` block-open stage) validates against the relaxed
    draft contract AND skips the decision projection: a decision read-model is for
    a *resolved* design, not an open block.
    """
    validate_design(body, draft=draft)  # non-evadable gate — raises before any write

    design_id = body["design_id"]
    design_path = memory_emit.emit(
        "designs", emitter, body, kind="design",
        namespace=[design_id], links=links, stamp=stamp,
        produced_by=produced_by,
    )

    decision_path = None
    if project_decision and not draft:
        decision_id = _decision_id_from_design(design_id)
        dbody = project_decision_body(body)
        # Same stamp semantics as the design so a re-emit overwrites the same
        # projected file in place (idempotent), never a duplicate.
        decision_path = memory_emit.emit(
            "decisions", emitter, dbody, kind="decision",
            namespace=[decision_id], links=links, stamp=stamp,
            produced_by=f"design:{design_id}",
        )

    # Log the store crossing (state change + interface crossing — AR-009 / the
    # stamped logging constraint). Parity with ticket_store's write logging.
    log.info("design_emit: %s %s written%s", "draft" if draft else "resolved",
             design_id, "" if decision_path is None else f" (+decision {_decision_id_from_design(design_id)})")

    return {"design_path": design_path, "decision_path": decision_path}


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Emit a DESIGN artifact (validated).")
    ap.add_argument("--emitter", default="cc.0")
    ap.add_argument("--stamp", default=None,
                    help="yyyymmdd.hhmmssuuuuuu (reuse to overwrite in place)")
    ap.add_argument("--links", default=None, help="JSON dict of semantic-id links")
    ap.add_argument("--produced-by", default=None,
                    help="backward edge: the intention/session that produced this design")
    ap.add_argument("--project-decision", action="store_true",
                    help="ALSO materialise a back-compat D-* into decisions/ "
                         "(default off — the decision view is projected on read by "
                         "design_store.iter_decision_view)")
    ap.add_argument("--draft", action="store_true",
                    help="block-open draft (/design): relaxed contract, no projection")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--body", help="inline JSON design body")
    g.add_argument("--body-file", help="path to a JSON design body file")
    args = ap.parse_args(argv)

    if args.body_file:
        with open(args.body_file) as f:
            body = json.load(f)
    elif args.body:
        body = json.loads(args.body)
    else:
        body = json.load(sys.stdin)

    links = json.loads(args.links) if args.links else None
    out = emit_design(body, emitter=args.emitter, links=links, stamp=args.stamp,
                      produced_by=args.produced_by,
                      project_decision=args.project_decision,
                      draft=args.draft)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    _main()
