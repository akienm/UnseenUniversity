"""Design-store reader + contract validator — the design artifact's schema home.

Companion to ``proof_store.py`` / ``ticket_store.py``: a DESIGN is emitted by
``devlab/claudecode/design_emit.py`` into ``<store>/designs/*.json`` with the
shared envelope (``id/emitter/namespace/category/kind/emitted_at/links/body``).
This module owns the READ side and the CONTRACT the write side enforces.

Why `design` is first-class (D-boundary-contract, architecture/workflow-levels,
SETTLED 2026-07-10): the dev-process artifact stack is
``INTENTION -> DESIGN -> TICKET``. A *design* is the shape that realizes an
intention, and a **decision** is no longer a standalone artifact type — it folds
in as a **fork-resolution recorded INSIDE a design** (``body.forks[]``, each fork
carrying its ``why`` — CP3). The Design->Ticket boundary therefore crosses a
*design*, not a bare decision.

THE CONTRACT (``validate_design``). A well-formed design body carries:
  - ``design_id``          Design-<slug>-YYYY-MM-DD (stable semantic id)
  - ``title``              one-line summary
  - ``intentions``         >=1 intention it realizes (intent:I-* ids or
                           present-tense contract statements) — the front edge
  - ``shape``              the architecture/design narrative
  - ``forks``              >=1 fork-decision, each ``{question, resolution, why}``
                           (options optional) — the folded-in decisions. A design
                           that resolved NO fork, or a fork with no ``why``, is
                           REJECTED: that is the anti-hollow lever (a design must
                           record what it decided and WHY, or it is a wish, not a
                           design).
  - ``proof_obligations``  the proof(s) this design must satisfy (proof-as-thread,
                           born with each sub-intention as its how-to-verify)
  - ``status`` / ``date``  lifecycle + provenance

Fields it SUBSUMES from the retired decision type (so the projected back-compat
decision — see design_emit — is a pure field-map, and no reader is stranded):
``spawned_tickets``, ``hypothesis``, ``measurement_signal``,
``validity_conditions``, ``text``.

Design rules honoured:
- **NO SQLITE / NO POSTGRES.** Pure filesystem read + in-memory validation.
- Reads are lock-free (atomic-replace files are always valid).
- Same ``UU_MEMORY_ROOT`` convention as proof_store/ticket_store, so tests
  redirect it with one monkeypatch.
- Kept import-light (no ``memory_emit`` dependency) so it stays importable in any
  packaged-device context — the WRITE side lives in devlab/claudecode/design_emit.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Iterator, Optional

from unseen_university.memory_root import memory_root as _memory_root

log = logging.getLogger(__name__)


class DesignValidationError(ValueError):
    """A design body violates the contract (``validate_design``)."""


# The fork sub-record's required keys. ``options`` (the alternatives considered)
# is recommended but not required — a fork can be a single forced move, but it
# must still say WHAT was resolved and WHY.
_FORK_REQUIRED = ("question", "resolution", "why")


def _designs_dir() -> Path:
    return _memory_root() / "designs"


def validate_design(body: dict, *, draft: bool = False) -> None:
    """Raise ``DesignValidationError`` unless ``body`` satisfies the design
    contract. Return ``None`` on success.

    This is the single non-evadable gate the write side (``emit_design``) calls
    BEFORE writing — so a hollow design cannot reach the store, whatever a skill's
    prose says (a soft "please run the validator" line is attacker-controlled).

    ``draft=True`` is the block-OPEN lifecycle stage (``/design``): the design has
    captured the intention it will realize but the forks are not resolved yet, so
    only ``design_id`` + ``intentions`` are required. It is the ONLY exemption from
    the fork contract, and it is not an escape hatch — a draft is explicitly *not*
    a claim of a resolved design; ``/sorted`` promotes it (``draft=False``) and the
    full contract, including ≥1 fork-with-why, then applies.
    """
    if not isinstance(body, dict):
        raise DesignValidationError(f"design body must be a dict, got {type(body).__name__}")

    did = body.get("design_id")
    if not isinstance(did, str) or not did.strip():
        raise DesignValidationError("design_id missing or empty")

    intentions = body.get("intentions")
    if not isinstance(intentions, list) or not any(
        isinstance(i, str) and i.strip() for i in intentions
    ):
        raise DesignValidationError(
            "intentions must be a non-empty list — a design must realize at least "
            "one intention"
        )

    if draft:
        return  # block-open stage: intention captured, forks not resolved yet

    for field in ("title", "shape"):
        v = body.get(field)
        if not isinstance(v, str) or not v.strip():
            raise DesignValidationError(f"{field} missing or empty")

    proof_obl = body.get("proof_obligations")
    if not isinstance(proof_obl, list):
        raise DesignValidationError(
            "proof_obligations must be a list (proof-as-thread — the obligations "
            "this design must satisfy; an empty list is allowed, a missing field "
            "is not)"
        )

    # ── The fork contract (the anti-hollow lever) ──────────────────────────────
    _validate_forks(body.get("forks"))


def _validate_forks(forks) -> None:
    """Enforce the fork contract: a resolved design must record ≥1 fork-decision,
    and every fork must carry its ``why`` (CP3). This is what makes ``design`` a
    design and not a wish — a block that resolved nothing, or resolved something
    without recording why, is refused.
    """
    if not isinstance(forks, list) or not forks:
        raise DesignValidationError(
            "a resolved design must record ≥1 fork (the decision(s) it folds in) — "
            "forks[] is empty or missing; a design that resolved no fork is not a design"
        )
    for i, fork in enumerate(forks):
        if not isinstance(fork, dict):
            raise DesignValidationError(f"forks[{i}] must be an object, got {type(fork).__name__}")
        for key in _FORK_REQUIRED:
            v = fork.get(key)
            if not isinstance(v, str) or not v.strip():
                raise DesignValidationError(
                    f"forks[{i}].{key} missing or empty — every fork must state its "
                    f"question, resolution, and why (there's always a why, CP3)"
                )


def iter_designs() -> Iterator[dict]:
    """Yield every design envelope in the store (unreadable files skipped)."""
    d = _designs_dir()
    if not d.exists():
        return
    for p in sorted(d.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # unreadable file — skip, never crash a read
            log.warning("design_store: unreadable file %s: %s", p.name, exc)
            continue
        if isinstance(rec, dict) and isinstance(rec.get("body"), dict):
            yield rec


def get_design(design_id: str) -> Optional[dict]:
    """Return the design envelope whose ``body.design_id`` matches, else None."""
    for rec in iter_designs():
        if (rec.get("body") or {}).get("design_id") == design_id:
            return rec
    return None


# ── The decision read-model (retires the write-time projection) ─────────────────
#
# A DESIGN is the source of truth; a *decision* is a fork folded into it. The
# broad decisions/ reader surface (context-load, validity_sweep, the /outcome
# list, …) predates design-first, so it reads decision-shaped records. Rather
# than MATERIALIZE a back-compat ``D-*`` file per design at write time (a second
# home that drifts — CLAUDE.md "one home"), we project on READ: this module owns
# the field-map, and ``iter_decision_view()`` merges live design-projections with
# the historical ``decisions/`` records (which stay readable — scope boundary).
#
# design_emit imports these two helpers so the projection has ONE definition,
# whether it's ever materialised or (default now) only ever viewed.


def decision_id_from_design(design_id: str) -> str:
    """Derive the projected decision id from a design id.

    ``Design-<slug>`` -> ``D-<slug>``; a bare ``D-*`` passes through; anything
    else is prefixed ``D-`` so the projection always has the ``D-`` handle the
    legacy readers key on.
    """
    if design_id.startswith("Design-"):
        return "D-" + design_id[len("Design-"):]
    if design_id.startswith("D-"):
        return design_id
    return "D-" + design_id


def project_decision_body(design_body: dict) -> dict:
    """Field-map a design body onto the legacy decision body every reader expects.

    Pure projection — the design already SUBSUMES these fields (see the module
    docstring), so nothing is invented here. Returns just the body; envelope
    framing (namespace/emitted_at) is added by ``iter_decision_view``/the emitter.
    """
    decision_id = decision_id_from_design(design_body.get("design_id", ""))
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
        # Outcome fields (written by /outcome onto the design) must ride the
        # projection too, or the decision-first outcome readers miss an outcome
        # the design already records.
        "outcome_date": design_body.get("outcome_date"),
        # Provenance breadcrumb: this decision is a projection, not a source.
        "projected_from_design": design_body.get("design_id"),
    }


def _decision_key(rec: dict) -> str:
    """The identity a decision record dedups on: namespace[0], else body.decision_id."""
    ns = rec.get("namespace")
    if isinstance(ns, list) and ns and isinstance(ns[0], str) and ns[0].strip():
        return ns[0]
    return (rec.get("body") or {}).get("decision_id") or ""


def iter_decision_view() -> Iterator[dict]:
    """Yield envelope-shaped decision records: every design projected once, plus
    the historical ``decisions/`` records that aren't a projection of some design.

    Each yielded record carries top-level ``namespace``/``emitted_at`` (what the
    readers sort + key on) and a ``body`` in decision shape — a design-projection
    is framed to look exactly like a materialised ``D-*`` envelope, so a reader
    can't tell (and needn't care) whether the record came from a design or a
    historical decision file.

    Dedup (advisor trap #2): a historical record is suppressed when it is a
    materialised projection (``body.projected_from_design`` set) OR its id equals
    a design's derived ``D-*`` id — so a design that was re-keyed from a former
    decision surfaces once (via the live design), never twice.
    """
    # 1. Project every design; remember the derived D-* ids to suppress dupes.
    projected_ids: set[str] = set()
    for rec in iter_designs():
        dbody = rec.get("body") or {}
        did = dbody.get("design_id")
        if not isinstance(did, str) or not did.strip():
            continue
        dec_id = decision_id_from_design(did)
        projected_ids.add(dec_id)
        yield {
            "id": rec.get("id"),
            "emitter": rec.get("emitter"),
            "namespace": [dec_id],
            "kind": "decision",
            "emitted_at": rec.get("emitted_at", ""),
            "links": rec.get("links"),
            "body": project_decision_body(dbody),
        }

    # 2. Historical decisions/ records that aren't a design's projection.
    d = _memory_root() / "decisions"
    if not d.exists():
        return
    for p in sorted(d.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("design_store: unreadable decision %s: %s", p.name, exc)
            continue
        if not isinstance(rec, dict) or not isinstance(rec.get("body"), dict):
            continue
        if rec["body"].get("projected_from_design"):
            continue  # a materialised projection — the design yields it above
        if _decision_key(rec) in projected_ids:
            continue  # a design re-keyed from this decision now owns the id
        yield rec
