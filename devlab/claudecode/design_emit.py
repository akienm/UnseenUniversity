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

  2. **A back-compat decision is PROJECTED into ``decisions/``** (unless
     ``project_decision=False``). The design is the SOURCE OF TRUTH; the
     projected ``D-*`` is a derived read-model so the broad existing decisions/
     reader surface (context-load 2a/3, validity_sweep, decision-rollup
     auto-close, web_server, igor cognition) keeps working while /sorted cuts
     over to design-first. This is transitional — retired by a follow-on once
     those readers point at ``designs/`` (note 2026-07-10, reader-gap decision).
     ``produced_by`` on the projection is ``design:<design_id>`` so the backward
     edge names the design as what to review if the projection is wrong.

NO SQLITE / NO POSTGRES — pure filesystem via the memory_emit chokepoint.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

# devlab/claudecode/design_emit.py -> repo root is three dirs up. Make the
# package importable without requiring an editable install to be active.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from devlab.claudecode import memory_emit  # noqa: E402
from unseen_university.design_store import validate_design  # noqa: E402


def _decision_id_from_design(design_id: str) -> str:
    """Derive the projected decision id: ``Design-<slug>-<date>`` -> ``D-<slug>-<date>``.

    Falls back to ``D-<design_id>`` when the design_id lacks the ``Design-`` prefix
    so the projection always has a ``D-`` handle the legacy readers grep for.
    """
    if design_id.startswith("Design-"):
        return "D-" + design_id[len("Design-"):]
    if design_id.startswith("D-"):
        return design_id
    return "D-" + design_id


def _project_decision_body(design_body: dict, decision_id: str) -> dict:
    """Field-map a design body onto the legacy decision body every reader expects.

    Pure projection: the design already SUBSUMES these fields
    (design_store contract), so nothing is invented here.
    """
    return {
        "decision_id": decision_id,
        "title": design_body.get("title", ""),
        "status": design_body.get("status", "open"),
        "date": design_body.get("date", ""),
        "author": design_body.get("author"),
        "spawned_tickets": design_body.get("spawned_tickets", []),
        "validity_conditions": design_body.get("validity_conditions", []),
        # The narrative readers render. Prefer the design's own text; fall back to
        # the shape so a projection is never empty.
        "text": design_body.get("text") or design_body.get("shape", ""),
        # Provenance breadcrumb: this decision is a projection, not a source.
        "projected_from_design": design_body.get("design_id"),
    }


def emit_design(body: dict, *, emitter: str = "cc.0", links: dict | None = None,
                stamp: str | None = None, produced_by: str | None = None,
                project_decision: bool = True, draft: bool = False) -> dict:
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
        dbody = _project_decision_body(body, decision_id)
        # Same stamp semantics as the design so a re-emit overwrites the same
        # projected file in place (idempotent), never a duplicate.
        decision_path = memory_emit.emit(
            "decisions", emitter, dbody, kind="decision",
            namespace=[decision_id], links=links, stamp=stamp,
            produced_by=f"design:{design_id}",
        )

    return {"design_path": design_path, "decision_path": decision_path}


def _main(argv=None):
    ap = argparse.ArgumentParser(description="Emit a DESIGN artifact (validated).")
    ap.add_argument("--emitter", default="cc.0")
    ap.add_argument("--stamp", default=None,
                    help="yyyymmdd.hhmmssuuuuuu (reuse to overwrite in place)")
    ap.add_argument("--links", default=None, help="JSON dict of semantic-id links")
    ap.add_argument("--produced-by", default=None,
                    help="backward edge: the intention/session that produced this design")
    ap.add_argument("--no-project-decision", action="store_true",
                    help="skip the transitional back-compat decision projection")
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
                      project_decision=not args.no_project_decision,
                      draft=args.draft)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    _main()
