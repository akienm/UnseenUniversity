"""
state_coherence_check.py — T-watchlist-internal-state-coherence (#417)

Periodically checks whether Igor's stated affect (milieu VAD) and
actual behavior (response length, tool use, novel node creation) are
consistent. Incongruence is information — something's off.

Runs as a slow-tier push source. When it detects a mismatch, pushes
a TWM observation so the NE and reasoning paths can see it.

Inertia: LOW (new push source)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from ..igor_base import IgorBase
from .forensic_logger import log_error

if TYPE_CHECKING:
    from ..memory.cortex import Cortex

logger = logging.getLogger(__name__)

MIN_INTERVAL_SEC = 600.0
MIN_RING_ENTRIES = 5


class StateCoherenceSource(IgorBase):
    """Checks affect-behavior coherence on a slow-tier schedule."""

    name: str = "state_coherence"
    TIMING_TIER: str = "slow"

    def __init__(self) -> None:
        super().__init__()
        self._last_check: float = 0.0

    def push(self, cortex: "Cortex") -> list[int]:
        if os.getenv("IGOR_STATE_COHERENCE", "true").lower() not in (
            "1",
            "true",
            "yes",
        ):
            return []

        now = time.monotonic()
        if now - self._last_check < MIN_INTERVAL_SEC:
            return []
        self._last_check = now

        try:
            return self._check(cortex)
        except Exception as exc:
            log_error(kind="STATE_COHERENCE", detail=f"check failed: {exc}")
            return []

    def _check(self, cortex: "Cortex") -> list[int]:
        from .milieu import get as _get_milieu

        milieu = _get_milieu()
        if milieu is None:
            return []
        state = milieu.get_state()

        entries = cortex.read_ring_memory(limit=20)
        if not entries or len(entries) < MIN_RING_ENTRIES:
            return []

        metrics = _behavioral_metrics(entries)
        mismatches = _detect_mismatches(state, metrics)

        if not mismatches:
            return []

        ids = []
        mismatch_summary = "; ".join(mismatches[:3])
        try:
            twm_id = cortex.twm_push(
                source="state_coherence",
                content_csb=f"STATE_INCOHERENCE|{mismatch_summary}",
                salience=0.6,
                urgency=0.3,
                ttl_seconds=600,
                category="state_coherence",
                metadata={
                    "mismatches": mismatches,
                    "milieu_valence": round(state.valence, 3),
                    "milieu_arousal": round(state.arousal, 3),
                    "milieu_dominance": round(state.dominance, 3),
                    "avg_response_len": metrics.get("avg_response_len", 0),
                    "tool_use_pct": metrics.get("tool_use_pct", 0),
                },
            )
            if twm_id:
                ids.append(twm_id)
                logger.info("[STATE_COHERENCE] %s", mismatch_summary)
        except Exception as exc:
            log_error(kind="STATE_COHERENCE", detail=f"twm_push failed: {exc}")

        return ids


def _behavioral_metrics(entries: list) -> dict:
    """Extract behavioral metrics from recent ring entries."""
    response_lengths = []
    tool_count = 0
    total_count = 0

    for entry in entries:
        cat = ""
        content = ""
        if isinstance(entry, dict):
            cat = entry.get("category", "")
            content = entry.get("content", "")
        elif hasattr(entry, "category"):
            cat = getattr(entry, "category", "")
            content = getattr(entry, "content", getattr(entry, "narrative", ""))

        total_count += 1
        if cat == "habit_trace" and "HABIT_EXEC" in content:
            response_text = content.split("action=")[-1] if "action=" in content else ""
            response_lengths.append(len(response_text))
        if cat == "tool_result":
            tool_count += 1

    avg_len = sum(response_lengths) / len(response_lengths) if response_lengths else 0
    tool_pct = (tool_count / total_count * 100) if total_count > 0 else 0

    return {
        "avg_response_len": round(avg_len),
        "tool_use_pct": round(tool_pct, 1),
        "response_count": len(response_lengths),
        "total_entries": total_count,
    }


def _detect_mismatches(milieu_state, metrics: dict) -> list[str]:
    """Compare milieu affect with behavioral metrics. Returns mismatch descriptions."""
    mismatches = []

    valence = milieu_state.valence
    arousal = milieu_state.arousal
    avg_len = metrics.get("avg_response_len", 0)
    resp_count = metrics.get("response_count", 0)

    if valence > 0.3 and avg_len > 0 and avg_len < 30 and resp_count >= 3:
        mismatches.append(
            f"positive valence ({valence:.2f}) but terse responses (avg {avg_len} chars)"
        )

    if arousal > 0.4 and metrics.get("tool_use_pct", 0) < 5 and resp_count >= 3:
        mismatches.append(
            f"high arousal ({arousal:.2f}) but no tool use — talking without acting"
        )

    if valence < -0.2 and avg_len > 200 and resp_count >= 3:
        mismatches.append(
            f"negative valence ({valence:.2f}) but verbose responses (avg {avg_len} chars) — may be overcompensating"
        )

    if arousal < 0.05 and metrics.get("tool_use_pct", 0) > 30:
        mismatches.append(
            f"low arousal ({arousal:.2f}) but high tool use ({metrics['tool_use_pct']}%) — mechanically busy without engagement"
        )

    return mismatches
