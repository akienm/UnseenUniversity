"""
reconsolidation.py — T-reconsolidation-on-recall

Every memory recall runs an implicit experiment: 'does this memory
still fit the current context?' In biology (Nader, Schafe, LeDoux),
recall makes a memory LABILE — editable for a window before re-
stabilizing. If the memory still fits, it re-stabilizes unchanged.
If it doesn't, the mismatch triggers revision.

In Igor's current substrate, recall is a read-only fetch: cached
facts never get re-tested, priors harden, and cached trust becomes a
liability. That's exactly the shape Akien's anti-PTSD epistemology
rejects. This module is the corrective primitive.

## Mechanism

- `mark_recalled(memory_ids)` — stamp in-process tracker with recall
  timestamp. Called by cortex.search at the end of a search pass.
  No DB write on the hot path.
- `confirm_recall(memory_id)` — downstream consumer confirmed the
  memory's prediction held. Removes from tracker. No DB write.
- `contradict_recall(cortex, memory_id, reason)` — downstream consumer
  observed a mismatch. Writes reconsolidation_flag=True and decays
  fit_confidence in memory metadata via cortex.store. ONE write per
  contradiction, not per recall. Contradictions are rare.
- `pending_ids()`, `pending_older_than(seconds)` — audit helpers for
  tracking unresolved recalls.

## CP grounding

- CP1 — no cached trust; every recall re-tests via the tracker
- CP2 — mismatches trigger learning (the contradict write), not error
- CP3 — contradict_recall requires a `reason` string (the 'why')
- CP6 — priors never harden into committed state; the tracker forces
  them to be checked before they're treated as confirmed

## Scope vs follow-ups

MVP (this ticket):
- In-process tracker + mark/confirm/contradict API
- cortex.search hook that fires mark_recalled
- Audit helpers

Follow-ups (separate tickets):
- Sleep consolidation sweep that processes flagged memories
- Hard-timeout auto-flag for abandoned recalls
- Wiring contradiction detection from downstream consumers
  (action_claim_verifier, response_coherence_inhibitor, etc.)
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .cortex import Cortex

logger = logging.getLogger(__name__)


# Tuning knobs

DEFAULT_FIT_CONFIDENCE: float = 1.0
FIT_CONFIDENCE_DECAY: float = 0.3
"""How much fit_confidence drops per contradiction. Bounded to >= 0."""

STALE_RECALL_SECONDS: int = 3600
"""Recalls older than this are considered stale by audit."""

# Exempt memory types — structural/identity nodes should never be
# subject to reconsolidation. They're the substrate, not beliefs.
EXEMPT_TYPES: frozenset[str] = frozenset(
    {"ROOT", "CORE_PATTERN", "IDENTITY", "ID", "RM"}
)


# In-process tracker. Module-level singleton. Thread-safe.
_lock = threading.Lock()
_recall_pending: dict[str, dict] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Mark / confirm / contradict ──────────────────────────────────────────────


def mark_recalled(memory_ids: list[str], context_hint: Optional[str] = None) -> int:
    """Record that these memory ids were just surfaced. Call from
    cortex.search at the end of a search pass. Returns the number of
    ids actually tracked (non-exempt)."""
    if not memory_ids:
        return 0
    tracked = 0
    ts = _now_iso()
    with _lock:
        for mid in memory_ids:
            if not mid:
                continue
            _recall_pending[mid] = {
                "recalled_at": ts,
                "context_hint": context_hint or "",
            }
            tracked += 1
    return tracked


def confirm_recall(memory_id: str) -> bool:
    """Downstream consumer confirmed the memory's prediction held.
    Remove from tracker. Returns True if the id was pending."""
    if not memory_id:
        return False
    with _lock:
        return _recall_pending.pop(memory_id, None) is not None


def contradict_recall(cortex: "Cortex", memory_id: str, reason: str) -> bool:
    """Downstream consumer observed a mismatch. Write reconsolidation
    flag + decayed fit_confidence to the memory's metadata via
    cortex.store. Returns True on successful write.

    CP3: reason must be non-empty — the 'why' of the contradiction.
    """
    if not memory_id:
        return False
    if not reason or not reason.strip():
        raise ValueError("contradict_recall requires non-empty reason (CP3)")

    with _lock:
        _recall_pending.pop(memory_id, None)

    try:
        mem = cortex.get(memory_id) if hasattr(cortex, "get") else None
    except Exception as exc:
        logger.warning("contradict_recall get(%s) failed: %s", memory_id, exc)
        return False

    if mem is None:
        logger.debug("contradict_recall: memory %s not found", memory_id)
        return False

    current_fit = float(
        (mem.metadata or {}).get("fit_confidence", DEFAULT_FIT_CONFIDENCE)
    )
    new_fit = max(0.0, current_fit - FIT_CONFIDENCE_DECAY)

    new_metadata = dict(mem.metadata or {})
    new_metadata["reconsolidation_flag"] = True
    new_metadata["fit_confidence"] = new_fit
    new_metadata["last_contradicted_at"] = _now_iso()
    existing_reasons = list(new_metadata.get("contradiction_reasons", []))
    existing_reasons.append(reason)
    new_metadata["contradiction_reasons"] = existing_reasons[-5:]  # cap at 5
    mem.metadata = new_metadata

    try:
        cortex.store(mem)
    except Exception as exc:
        logger.warning("contradict_recall store(%s) failed: %s", memory_id, exc)
        return False
    return True


# ── Audit helpers ────────────────────────────────────────────────────────────


def pending_count() -> int:
    with _lock:
        return len(_recall_pending)


def pending_ids() -> list[str]:
    with _lock:
        return list(_recall_pending.keys())


def pending_older_than(seconds: int = STALE_RECALL_SECONDS) -> list[str]:
    """Return pending recall ids whose recalled_at is older than N seconds.
    Audit signal for abandoned recalls that never got confirmed or
    contradicted (stale hot path)."""
    cutoff = datetime.now(timezone.utc).timestamp() - seconds
    stale: list[str] = []
    with _lock:
        for mid, entry in _recall_pending.items():
            try:
                ts = datetime.fromisoformat(entry["recalled_at"]).timestamp()
            except Exception:
                continue
            if ts < cutoff:
                stale.append(mid)
    return stale


def clear_pending() -> None:
    """Test/debug utility — wipe the tracker."""
    with _lock:
        _recall_pending.clear()


# ── Hook called from cortex.search ──────────────────────────────────────────


def hook_search_results(results: list, query: str = "") -> int:
    """Called at the end of cortex.search with the final result list.
    Extracts memory ids, filters exempt types, marks the rest as
    pending recall.

    Returns the number of ids tracked. Never raises — failures degrade
    to 0."""
    try:
        ids: list[str] = []
        for r in results or []:
            mid = getattr(r, "id", None)
            if not mid:
                continue
            mtype = getattr(r, "memory_type", None)
            if mtype is not None:
                mtype_val = getattr(mtype, "value", mtype)
                if str(mtype_val).upper() in EXEMPT_TYPES:
                    continue
            ids.append(mid)
        return mark_recalled(ids, context_hint=f"search:{query[:40]}")
    except Exception as exc:
        logger.debug("hook_search_results failed: %s", exc)
        return 0
