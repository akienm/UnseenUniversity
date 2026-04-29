"""
approach_frame_audit.py — T-igor-self-audit-approach-frame

Periodic night-time audit that scans rule-shaped memories (PROCEDURAL,
CORE_PATTERN) for avoidance-frame language and writes pending reframe
candidates for Akien/CC review.

Goal: maximize approach-framed instructions ("do this") and minimize
avoidance-framed instructions ("don't do that") across Igor's rule-shaped
memory corpus. Parallel to T-cc-memory-approach-frame-sweep which handles
CC's file-based feedback corpus.

Schedule: nighttime (22:00-07:00) + 3-day cooldown by default. Configurable
via IGOR_APPROACH_FRAME_AUDIT_DAYS, IGOR_APPROACH_FRAME_AUDIT_SAMPLE,
IGOR_APPROACH_FRAME_AUDIT_TOP_N, IGOR_APPROACH_FRAME_AUDIT_THRESHOLD env
vars. Disable entirely with IGOR_APPROACH_FRAME_AUDIT=false.

Drain: candidates are FACTUAL memories tagged
metadata.pending_approach_reframe=True with source_memory_id pointing back
to the audited row. Akien/CC drain the queue during /day-close and decide
per memory: reframe text, delete, or pass.

Inertia: LOW — additive push source, doesn't touch brainstem.
"""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from ..igor_base import IgorBase
from ..memory.models import Memory, MemoryType
from .forensic_logger import log_error

if TYPE_CHECKING:
    from ..memory.cortex import Cortex

logger = logging.getLogger(__name__)

AUDIT_WINDOW_START = 22
AUDIT_WINDOW_END = 7
DEFAULT_COOLDOWN_DAYS = 3.0
DEFAULT_SAMPLE_SIZE = 50
DEFAULT_TOP_N = 10
DEFAULT_SCORE_THRESHOLD = 0.02

RULE_SHAPED_TYPES = (MemoryType.PROCEDURAL, MemoryType.CORE_PATTERN)

_AVOIDANCE_PATTERNS = re.compile(
    r"(?i)\b(?:"
    r"don't|do not|never|must not|mustn't|cannot|can't|"
    r"avoid|stop|"
    r"shouldn't|should not|"
    r"refuse to|fail to|"
    r"no longer|"
    r"prevent|prohibit|forbidden|disallowed"
    r")\b"
)


def _in_audit_window(hour: int) -> bool:
    """True if the given hour falls in the nighttime audit window (22-07)."""
    if AUDIT_WINDOW_START <= AUDIT_WINDOW_END:
        return AUDIT_WINDOW_START <= hour < AUDIT_WINDOW_END
    return hour >= AUDIT_WINDOW_START or hour < AUDIT_WINDOW_END


def _score_avoidance(narrative: str) -> tuple[int, float]:
    """Return (hit_count, normalized_score) for avoidance markers in narrative.

    normalized_score = hits / max(word_count, 1) so longer narratives don't
    win by accumulation alone.
    """
    if not narrative:
        return (0, 0.0)
    hits = len(_AVOIDANCE_PATTERNS.findall(narrative))
    words = max(len(narrative.split()), 1)
    return (hits, hits / words)


class ApproachFrameAuditSource(IgorBase):
    """Push source that periodically audits rule-shaped memories for avoidance framing.

    Runs at night (22:00-07:00) on a 3-day cooldown. Pulls memories of types
    PROCEDURAL + CORE_PATTERN, scores each by avoidance-marker density,
    writes top N over threshold as FACTUAL memories tagged
    pending_approach_reframe for Akien/CC review.
    """

    name: str = "approach_frame_audit"
    TIMING_TIER: str = "slow"

    def __init__(self) -> None:
        super().__init__()
        self._last_audit_ts: Optional[float] = None
        self._last_check_ts: float = 0.0

    def push(self, cortex: "Cortex") -> list[int]:
        if os.getenv("IGOR_APPROACH_FRAME_AUDIT", "true").lower() not in (
            "1",
            "true",
            "yes",
        ):
            return []

        now = time.monotonic()
        if now - self._last_check_ts < 60.0:
            return []
        self._last_check_ts = now

        if not _in_audit_window(datetime.now().hour):
            return []

        cooldown_days = float(
            os.getenv("IGOR_APPROACH_FRAME_AUDIT_DAYS", str(DEFAULT_COOLDOWN_DAYS))
        )
        if self._last_audit_ts is not None:
            hours_since = (now - self._last_audit_ts) / 3600.0
            if hours_since < cooldown_days * 24:
                return []

        return self._run_audit(cortex, now)

    def _run_audit(self, cortex: "Cortex", now: float) -> list[int]:
        """Sample rule-shaped memories, score, write top-N pending reframes."""
        self._last_audit_ts = now
        ts = datetime.now(timezone.utc).isoformat()

        sample_size = int(
            os.getenv("IGOR_APPROACH_FRAME_AUDIT_SAMPLE", str(DEFAULT_SAMPLE_SIZE))
        )
        top_n = int(os.getenv("IGOR_APPROACH_FRAME_AUDIT_TOP_N", str(DEFAULT_TOP_N)))
        threshold = float(
            os.getenv(
                "IGOR_APPROACH_FRAME_AUDIT_THRESHOLD", str(DEFAULT_SCORE_THRESHOLD)
            )
        )

        candidates: list[tuple[Memory, int, float]] = []
        scanned = 0
        for mt in RULE_SHAPED_TYPES:
            try:
                rows = cortex.get_by_type(mt, limit=sample_size)
            except Exception as exc:
                log_error(
                    kind="APPROACH_FRAME_AUDIT",
                    detail=f"get_by_type({mt.value}): {exc}",
                )
                continue
            scanned += len(rows)
            for mem in rows:
                if mem.metadata and (
                    mem.metadata.get("pending_approach_reframe")
                    or mem.metadata.get("approach_frame_audit_source")
                ):
                    continue
                hits, score = _score_avoidance(mem.narrative or "")
                if score >= threshold and hits > 0:
                    candidates.append((mem, hits, score))

        candidates.sort(key=lambda c: c[2], reverse=True)
        top = candidates[:top_n]

        ids: list[int] = []
        for mem, hits, score in top:
            try:
                excerpt = (mem.narrative or "")[:200].replace("\n", " ")
                reframe_memory = Memory(
                    narrative=(
                        f"Audit candidate for approach-frame reframe — source memory "
                        f"{mem.id} has {hits} avoidance markers (score={score:.3f}). "
                        f"Excerpt: {excerpt}"
                    ),
                    memory_type=MemoryType.FACTUAL,
                    metadata={
                        "pending_approach_reframe": True,
                        "approach_frame_audit_source": True,
                        "source_memory_id": mem.id,
                        "source_memory_type": mem.memory_type.value,
                        "avoidance_hit_count": hits,
                        "avoidance_score": round(score, 4),
                        "audit_ts": ts,
                    },
                    source="self_edit",
                )
                stored = cortex.store(reframe_memory)
                if stored and stored.id:
                    twm_id = cortex.twm_push(
                        source="approach_frame_audit",
                        content_csb=(
                            f"PENDING_APPROACH_REFRAME|src={mem.id}|score={score:.3f}"
                        ),
                        salience=0.25,
                        urgency=0.0,
                        ttl_seconds=86400,
                        category="audit_pending",
                        metadata={
                            "reframe_memory_id": stored.id,
                            "source_memory_id": mem.id,
                        },
                    )
                    if twm_id:
                        ids.append(twm_id)
            except Exception as exc:
                log_error(
                    kind="APPROACH_FRAME_AUDIT",
                    detail=f"store/push for {mem.id}: {exc}",
                )

        logger.info(
            "[APPROACH_FRAME_AUDIT] scanned=%d candidates=%d written=%d",
            scanned,
            len(candidates),
            len(top),
        )
        return ids

    def last_audit_age_hours(self) -> Optional[float]:
        """Hours since last audit pass, or None if never run."""
        if self._last_audit_ts is None:
            return None
        return (time.monotonic() - self._last_audit_ts) / 3600.0
