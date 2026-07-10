"""Intention-store reader + contract validator — the intention artifact's schema home.

Companion to ``design_store.py`` / ``proof_store.py``: an INTENTION is emitted by
``devlab/claudecode/intention_emit.py`` into ``<store>/intentions/*.json`` with the
shared envelope. This module owns the READ side and the CONTRACT the write side
enforces.

Why the intention is first-class and DECONSTRUCTED (architecture/workflow-levels,
L1→L2; T-intention-capture-deconstruct-skill): the dev-process stack is
``INTENTION -> DESIGN -> TICKET``, and the front boundary had nothing durable —
intentions were hand-authored prose in ``akien/outbox/IntentionsOutline.txt`` (which
stays the Akien-authored SOURCE; see project_intentions_outline_is_source). The
capture/deconstruct skill turns an intention into a durable artifact and breaks it
into HIERARCHICAL sub-intentions, each paired with its PROOF-OBLIGATION — proof is a
THREAD born here (settled 2026-07-10), carried intention -> ticket -> prereg -> prove.

TWO CONTRACT TIERS (mirrors design_store's draft/full):
  - **base** (``deconstructed=False``): the captured global intention —
    ``intention_id`` + ``statement`` (the present-tense "I intend that…" contract).
    The existing flat ``I-*`` records validate at this tier, so nothing is
    retroactively broken.
  - **deconstructed** (``deconstructed=True``): the skill's output — additionally
    requires ``why``, ``how_to_verify``, ``constraints`` (list), and
    ``sub_intentions`` (≥1), where **every sub-intention carries its own
    ``proof_obligation``** (plus ``statement`` + ``why``). A sub-intention with no
    proof-obligation is REJECTED: that is the anti-hollow lever — a deconstruction
    that cannot say how each piece will be verified is a wish-list, not a plan, and
    it breaks the proof thread the whole pipeline rides on.

Design rules honoured:
- **NO SQLITE / NO POSTGRES.** Pure filesystem read + in-memory validation.
- Same ``UU_MEMORY_ROOT`` convention as design_store/proof_store.
- Import-light (no ``memory_emit`` dependency) so it stays importable anywhere; the
  WRITE side lives in devlab/claudecode/intention_emit.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterator, Optional

from unseen_university.memory_root import memory_root as _memory_root

log = logging.getLogger(__name__)


class IntentionValidationError(ValueError):
    """An intention body violates the contract (``validate_intention``)."""


# Every sub-intention must state itself, its why, and — the load-bearing field —
# the proof-obligation that threads to prereg/prove.
_SUBINTENTION_REQUIRED = ("statement", "why", "proof_obligation")


def _intentions_dir() -> Path:
    return _memory_root() / "intentions"


def validate_intention(body: dict, *, deconstructed: bool = False) -> None:
    """Raise ``IntentionValidationError`` unless ``body`` satisfies the contract.
    Return ``None`` on success.

    The write side (``emit_intention``) calls this BEFORE writing, so a hollow
    intention cannot reach the store whatever a skill's prose says. ``deconstructed``
    selects the tier (see module docstring).
    """
    if not isinstance(body, dict):
        raise IntentionValidationError(
            f"intention body must be a dict, got {type(body).__name__}")

    iid = body.get("intention_id")
    if not isinstance(iid, str) or not iid.strip():
        raise IntentionValidationError("intention_id missing or empty")

    statement = body.get("statement")
    if not isinstance(statement, str) or not statement.strip():
        raise IntentionValidationError(
            "statement missing or empty — an intention IS its present-tense "
            "'I intend that…' contract")

    if not deconstructed:
        return  # captured-but-not-yet-deconstructed (base tier)

    for field in ("why", "how_to_verify"):
        v = body.get(field)
        if not isinstance(v, str) or not v.strip():
            raise IntentionValidationError(f"{field} missing or empty")

    if not isinstance(body.get("constraints"), list):
        raise IntentionValidationError(
            "constraints must be a list (an empty list is allowed, a missing "
            "field is not)")

    # ── The sub-intention ⊗ proof-obligation contract (the anti-hollow lever) ──
    # STUB (T-intention-capture-deconstruct-skill, commit A): enforcement is a
    # no-op here so the scaffold + emit land first; commit B flips it on and the
    # proof node test_subintention_missing_proof_obligation_rejected goes red->green.
    _validate_sub_intentions(body.get("sub_intentions"))


def _validate_sub_intentions(subs) -> None:
    """STUB — replaced with real enforcement in commit B (proof flip)."""
    return None


def iter_intentions() -> Iterator[dict]:
    """Yield every intention envelope in the store (unreadable files skipped)."""
    d = _intentions_dir()
    if not d.exists():
        return
    for p in sorted(d.glob("*.json")):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:  # unreadable file — skip, never crash a read
            log.warning("intention_store: unreadable file %s: %s", p.name, exc)
            continue
        if isinstance(rec, dict) and isinstance(rec.get("body"), dict):
            yield rec


def get_intention(intention_id: str) -> Optional[dict]:
    """Return the intention envelope whose ``body.intention_id`` matches, else None."""
    for rec in iter_intentions():
        if (rec.get("body") or {}).get("intention_id") == intention_id:
            return rec
    return None
