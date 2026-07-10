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
