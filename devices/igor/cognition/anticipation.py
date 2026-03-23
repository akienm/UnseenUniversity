"""
anticipation.py — Anticipation pull: predict closure valence for candidate tickets.

Part of Igor self-driving (T-anticipation-pull). When the foreman scans the
work queue, this module weights ticket selection by *predicted* closure valence —
what completing this ticket is likely to feel like, based on past closures.

Mechanism:
  record_closure(ticket_id, tags, valence) — called after each sprint completion
  predict_valence(tags) → float             — weighted average from matching history
  weighted_ticket_score(priority, tags) → float — sort key for foreman selection

Storage: JSON at ~/.TheIgors/cc_channel/closure_history.json
  Simple, crash-safe, no Igor DB dependency. One entry per closed ticket.

Calibration:
  IGOR_ANTICIPATION_WEIGHT (default 0.3) — how strongly predicted valence pulls
  vs priority. Lower weight = boredom/priority dominates; higher = Igor steers
  more by what it wants. Intentionally weaker than boredom push so anticipation
  is directional but not overriding.

  IGOR_ANTICIPATION_HISTORY_MAX (default 50) — how many closures to keep.
  Oldest evicted when full.

Tag matching: a candidate ticket's tags are compared to historical closure tags.
  Overlap ≥1 tag → that closure contributes to the prediction. Weighted by
  recency (exponential decay with half-life = HISTORY_MAX/2 entries).

Gate: IGOR_ANTICIPATION_ENABLED (default true) — can be disabled for testing.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..paths import paths

# ── Config ──────────────────────────────────────────────────────────────────

_WEIGHT = float(os.getenv("IGOR_ANTICIPATION_WEIGHT", "0.3"))
_HISTORY_MAX = int(os.getenv("IGOR_ANTICIPATION_HISTORY_MAX", "50"))
_HISTORY_HALF_LIFE = _HISTORY_MAX / 2  # recency decay


def enabled() -> bool:
    return os.getenv("IGOR_ANTICIPATION_ENABLED", "true").lower() == "true"


def _history_path() -> Path:
    return paths().cc_channel / "closure_history.json"


# ── Persistence ─────────────────────────────────────────────────────────────


def _load() -> list[dict]:
    """Load closure history. Returns [] if missing or corrupt."""
    p = _history_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save(history: list[dict]) -> None:
    """Persist closure history (ring: newest-last, evict oldest)."""
    trimmed = history[-_HISTORY_MAX:]
    try:
        _history_path().parent.mkdir(parents=True, exist_ok=True)
        _history_path().write_text(json.dumps(trimmed, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── Public API ───────────────────────────────────────────────────────────────


def record_closure(ticket_id: str, tags: list[str], valence: float) -> None:
    """
    Record that a ticket was completed and the milieu spike had this valence.

    Called at sprint completion (step 8) — the valence from the dopamine signal
    (D185, narrative_engine._process_gaps) or a fixed positive proxy if the turn
    did not produce a measurable gap closure.

    ticket_id — e.g. "T-routing-cluster-router"
    tags      — list of strings from queue.json; may be empty
    valence   — [-1.0, 1.0]; positive = felt good to finish, negative = felt bad
    """
    history = _load()
    history.append(
        {
            "ticket_id": ticket_id,
            "tags": list(tags),
            "valence": float(valence),
            "ts": time.time(),
        }
    )
    _save(history)


def predict_valence(tags: list[str]) -> float:
    """
    Predict the closure valence for a candidate ticket with these tags.

    Returns a weighted average of historical closures that share ≥1 tag,
    weighted by recency (recent closures count more). Returns 0.0 if there
    is no matching history (neutral: unknown, not bad).

    tags — candidate ticket's tags (may be empty → no history match → 0.0)
    """
    if not tags:
        return 0.0

    history = _load()
    if not history:
        return 0.0

    tag_set = set(tags)
    n = len(history)

    weight_sum = 0.0
    valence_sum = 0.0

    for i, entry in enumerate(history):
        entry_tags = set(entry.get("tags", []))
        if not (tag_set & entry_tags):
            continue  # no overlap — skip
        # Recency weight: older entries decay exponentially
        age = n - i  # 1 = most recent, n = oldest
        recency = 2.0 ** (-age / _HISTORY_HALF_LIFE)
        weight_sum += recency
        valence_sum += recency * float(entry.get("valence", 0.0))

    if weight_sum == 0.0:
        return 0.0

    return valence_sum / weight_sum


def weighted_ticket_score(priority: int, tags: list[str]) -> float:
    """
    Compute a sort key for foreman ticket selection.

    Lower score = pick this ticket first.
    Base: priority (1 = highest). Anticipation subtracts from it so a highly
    anticipated ticket gets a lower (better) score than its raw priority.

    Formula: priority - ANTICIPATION_WEIGHT * predicted_valence
    Example: priority=2, predicted_valence=0.8, weight=0.3 → score=1.76
             priority=1, predicted_valence=-0.5, weight=0.3 → score=1.15
             (bored-of-it priority=1 loses to motivated priority=2)
    """
    if not enabled():
        return float(priority)
    v = predict_valence(tags)
    return float(priority) - _WEIGHT * v


def record_completion(input_text: str, reply_text: str, cortex) -> None:
    """
    Action-completion hookpoint — called after each non-impulse reply.

    Revised design (T-anticipation-pull, 2026-03-23):
    HOOKPOINT: reply fires = completion signal. No discrete ticket close needed.
    The chain reaches its natural endpoint; salience drops as new content fills
    attentional space. No explicit "close" event required.

    Classification via NE surprise delta (from most recent NE_SURPRISE ring entry):
      ORDINARY (delta < 0.4):  COMPLETION_ACK ring entry — no durable store.
      NOTEWORTHY (delta >= 0.4): COMPLETION_NOTEWORTHY ring entry — flags for
        NE re-engagement; EPISODIC store decided downstream by the NE.

    Never raises — action-completion is advisory, not load-bearing.
    """
    if cortex is None:
        return
    try:
        # Extract surprise delta from most recent NE_SURPRISE ring entry
        _delta = 0.0
        try:
            recent = cortex.read_ring_memory(limit=5, category="ne_prediction")
            for entry in recent:
                txt = (
                    entry.get("content", "") if isinstance(entry, dict) else str(entry)
                )
                if "NE_SURPRISE" in txt and "delta=" in txt:
                    parts = dict(p.split("=", 1) for p in txt.split("|") if "=" in p)
                    _delta = float(parts.get("delta", 0.0))
                    break
        except Exception:
            _delta = 0.0

        _snippet = input_text[:60].replace("|", "/")
        if _delta >= 0.4:
            # Noteworthy: prediction violated → flag for NE deeper look
            cortex.write_ring(
                f"COMPLETION_NOTEWORTHY|delta={_delta:.2f}|input={_snippet}",
                category="completion_trace",
            )
        else:
            # Ordinary: chain satisfied, no residual impulse; ring ACK only
            cortex.write_ring(
                f"COMPLETION_ACK|ordinary|delta={_delta:.2f}|input={_snippet}",
                category="completion_trace",
            )
    except Exception:
        pass  # advisory — never raises


def history_summary() -> str:
    """Return a human-readable summary of closure history (for diagnostics)."""
    history = _load()
    if not history:
        return "closure history: empty"
    recent = history[-5:]
    lines = [f"closure history: {len(history)} entries (last {len(recent)}):"]
    for e in recent:
        ts = e.get("ts", 0.0)
        age_h = (time.time() - ts) / 3600 if ts else 0
        lines.append(
            f"  [{e['ticket_id']}] v={e['valence']:+.2f} tags={e['tags']} "
            f"({age_h:.1f}h ago)"
        )
    return "\n".join(lines)
