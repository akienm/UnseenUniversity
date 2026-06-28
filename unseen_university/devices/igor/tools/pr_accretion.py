"""
pr_accretion.py — T-pr-accretion.

Per-turn online accretion into a persistent-relationship's facia subtree.
Every Akien-turn writes one EPISODIC memory linked to PR_AKIEN via
metadata.pr_facia_id. This is the "online encoding" half of the
hippocampal-cortical loop; the "offline consolidation" half is
T-pr-consolidation, which walks recent accretions, clusters them into
running themes, and updates cumulative_investment_weight.

Biomimetic framing: every interaction with a person you have a persistent-
relationship with creates SOME trace, even if minor. Most are "we said
hello, here's the gist." A few are load-bearing (commitments made,
decisions reached, emotional shifts). The accretion is fast, noisy,
low-latency. Consolidation cleans it up later.

Memory shape: gist+verbatim split (Fuzzy Trace Theory, mirrors f0ad6dab).
The narrative carries a brief human-readable summary; the metadata holds
the verbatim user text and Igor reply at full fidelity.

This module is best-effort throughout — accretion failures must NEVER
break a turn. Every entry point catches and logs.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _accretion_log(stage: str, **fields) -> None:
    """Forensic log for accretion events. Never raises."""
    try:
        from ..paths import paths as _paths

        line = f"{_now_iso()} {stage}"
        for k, v in fields.items():
            line += f" {k}={str(v)[:200].replace(chr(10), ' ')}"
        log_path = _paths().logs / "pr_accretion.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a") as f:
            f.write(line + "\n")
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind="SILENT_EXCEPT", detail=f"pr_accretion.py:46: {_exc}")


def _summarize_exchange(user_text: str, igor_reply: str, max_len: int = 240) -> str:
    """Human-readable narrative summary for the accretion memory.

    Just trims the two sides to fit; not a real LLM summary. The full
    untruncated content lives in metadata. The narrative exists for
    semantic search and human inspection.
    """
    u = (user_text or "").strip().replace("\n", " ")
    i = (igor_reply or "").strip().replace("\n", " ")
    half = max(40, (max_len - 20) // 2)
    return f"akien: {u[:half]} | igor: {i[:half]}"


def pr_accrete(
    facia_id: str,
    content_type: str,
    narrative: str,
    metadata: Optional[dict] = None,
) -> Optional[str]:
    """Generic accretion entry. Creates a new memory linked to the facia.

    Returns the new memory's id on success, None on failure (best-effort).
    """
    try:
        from ..memory.cortex import Cortex
        from ..memory.models import Memory, MemoryType

        cortex = Cortex(None)
        mem_id = f"PRA_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        meta = dict(metadata or {})
        meta["pr_facia_id"] = facia_id
        meta["content_type"] = content_type
        meta["accreted_at"] = _now_iso()
        meta.setdefault("provenance", "T-pr-accretion")

        mem = Memory(
            id=mem_id,
            narrative=narrative,
            memory_type=MemoryType.EPISODIC,
            metadata=meta,
            portable=False,  # relationship traces are instance-local
            source="pr_accretion",
            context_of_encoding=f"persistent_relationship={facia_id}",
        )
        cortex.store(mem)
        _accretion_log(
            "accrete",
            mem_id=mem_id,
            facia_id=facia_id,
            content_type=content_type,
            narrative_len=len(narrative),
        )
        return mem_id
    except Exception as e:
        _accretion_log("accrete_failed", facia_id=facia_id, error=str(e))
        return None


def pr_accrete_exchange(
    facia_id: str,
    user_text: str,
    igor_reply: str,
    thread_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    author: str = "user",
) -> Optional[str]:
    """Accrete a single conversational exchange (user message + Igor reply).

    Stores both sides verbatim in metadata so future retrieval can quote
    fidelity-critical content (paths, code, names) from past exchanges
    without reconstructing from a truncated narrative.
    """
    narrative = _summarize_exchange(user_text, igor_reply)
    metadata = {
        "user_text": user_text or "",
        "igor_reply": igor_reply or "",
        "thread_id": thread_id or "",
        "turn_id": turn_id or "",
        "author": author,
        "user_char_len": len(user_text or ""),
        "igor_char_len": len(igor_reply or ""),
    }
    return pr_accrete(
        facia_id=facia_id,
        content_type="exchange",
        narrative=narrative,
        metadata=metadata,
    )


def pr_accrete_marker(
    facia_id: str,
    marker_text: str,
    why: str = "",
    thread_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> Optional[str]:
    """Accrete an explicit 'this matters' marker.

    Triggered when the user says 'remember', 'important', 'don't forget',
    'this matters', or similar. The marker text is the salient excerpt;
    why explains why this accretion fired.
    """
    narrative = f"[marker] {marker_text[:200]}"
    metadata = {
        "marker_text": marker_text or "",
        "why": why,
        "thread_id": thread_id or "",
        "turn_id": turn_id or "",
    }
    return pr_accrete(
        facia_id=facia_id,
        content_type="marker",
        narrative=narrative,
        metadata=metadata,
    )


def pr_accrete_commitment(
    facia_id: str,
    commitment_text: str,
    goal_id: str,
    thread_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> Optional[str]:
    """Accrete a commitment Igor made to the relationship-partner.

    Hooks into the reply-obligation-fork flow — when goal_adopt fires
    with awaiting_reply, the dispatch path also calls this so the
    commitment lands in the relationship subtree, not just in the goal
    system.
    """
    narrative = f"[commitment] {commitment_text[:200]}"
    metadata = {
        "commitment_text": commitment_text or "",
        "goal_id": goal_id,
        "thread_id": thread_id or "",
        "turn_id": turn_id or "",
    }
    return pr_accrete(
        facia_id=facia_id,
        content_type="commitment",
        narrative=narrative,
        metadata=metadata,
    )


# ── Detection helpers ────────────────────────────────────────────────────────

# Phrases that mark explicit "this matters" intent. Narrow on purpose —
# false positives create accretion noise. Better to miss a marker than to
# over-mark every routine sentence.
_MARKER_PATTERNS = (
    "remember this",
    "remember that",
    "don't forget",
    "do not forget",
    "this matters",
    "this is important",
    "important:",
    "key point",
    "note this",
    "make a note",
)


def detect_marker(text: str) -> Optional[str]:
    """Return the matched marker phrase if the text contains a marker
    pattern, else None. Case-insensitive."""
    if not text:
        return None
    lower = text.lower()
    for phrase in _MARKER_PATTERNS:
        if phrase in lower:
            return phrase
    return None


# ── Query helpers ────────────────────────────────────────────────────────────


def pr_recent_accretions(facia_id: str, limit: int = 20) -> list:
    """Return recent accretions linked to a facia. Most recent first.

    Used by tests and (eventually) by consolidation passes that walk the
    day's accretions to integrate them.
    """
    try:
        from ..memory.cortex import Cortex

        cortex = Cortex(None)
        with cortex._conn() as conn:
            rows = conn.execute(
                "SELECT id, narrative, metadata FROM memories "
                "WHERE memory_type = %s "
                "AND metadata @> jsonb_build_object('pr_facia_id', %s::text) "
                "ORDER BY id DESC LIMIT %s",
                ("EPISODIC", facia_id, limit),
            ).fetchall()
    except Exception as e:
        _accretion_log("recent_failed", facia_id=facia_id, error=str(e))
        return []

    import json as _json

    out = []
    for row in rows:
        _id = row[0] if not hasattr(row, "keys") else row["id"]
        _narr = row[1] if not hasattr(row, "keys") else row["narrative"]
        _raw_meta = row[2] if not hasattr(row, "keys") else row["metadata"]
        if isinstance(_raw_meta, dict):
            _meta = _raw_meta
        elif isinstance(_raw_meta, str):
            try:
                _meta = _json.loads(_raw_meta)
            except Exception:
                _meta = {}
        else:
            _meta = {}
        out.append({"id": _id, "narrative": _narr, "metadata": _meta})
    return out
