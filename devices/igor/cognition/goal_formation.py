"""
goal_formation.py — T-goal-formation-from-conversation (#427) — recurrence detection half

Goal formation has three paths in the EPIC body:
  1. EXPLICIT  — handled today by goal_adopt + On-it habit
  2. RECURRENCE — "Akien keeps returning to X" — THIS MODULE (MVP)
  3. AGREEMENT — partially handled by reply-obligation-fork

Validation ("is this candidate a real goal?") is a separate sub-slice
that consumes the experiment primitive — file as a follow-up ticket.

## What this module does

Scan recent EPISODIC + ring memories, cluster them by simple keyword
overlap, and surface clusters whose recurrence crosses a threshold as
*goal_formation_candidates*. Push the strongest candidate (one at a
time, like BoredomSource) to TWM with cp1_provisional=True.

## Axiomatic grounding

- **CP1** — the candidate is provisional. The TWM marker says so. Igor
  may confirm next turn, or let it decay.
- **CP2** — a discarded candidate is not a failure; it's data about what
  doesn't crystallize.
- **CP3** — every candidate carries the evidence (recurring tokens,
  source memory ids, first/last seen) so the why is auditable.
- **CP6** — formation candidates do NOT auto-create goal facia in MVP.
  Promotion to a real goal requires either confirmation (next turn) or
  an experiment outcome (later sub-slice). Cognition stays in the loop.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from ..memory.cortex import Cortex

logger = logging.getLogger(__name__)


# Tuning knobs (CP2: change freely as we learn what works)
DEFAULT_LOOKBACK_DAYS: int = 7
DEFAULT_MIN_RECURRENCE: int = 3
DEFAULT_MIN_TOKEN_LEN: int = 4
DEFAULT_MAX_CANDIDATES: int = 1  # one push per scan, like BoredomSource

_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]+")

# Tokens that are too generic to count as a recurring topic.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "here",
        "this",
        "that",
        "with",
        "from",
        "have",
        "what",
        "when",
        "where",
        "would",
        "could",
        "should",
        "about",
        "there",
        "their",
        "they",
        "them",
        "then",
        "than",
        "into",
        "your",
        "yours",
        "mine",
        "ours",
        "just",
        "like",
        "want",
        "make",
        "made",
        "really",
        "thing",
        "things",
        "going",
        "still",
        "even",
        "more",
        "less",
        "much",
        "many",
        "some",
        "any",
        "all",
        "every",
        "each",
        "both",
        "either",
        "other",
        "very",
        "well",
        "back",
        "been",
        "being",
        "doing",
        "done",
        "said",
        "says",
        "tell",
        "told",
        "know",
        "knew",
        "think",
        "thought",
        "today",
        "tomorrow",
        "yesterday",
        "right",
        "left",
        "thanks",
        "thank",
        "yeah",
        "okay",
        "sure",
        "maybe",
        "actually",
    }
)


# ── Candidate dataclass ──────────────────────────────────────────────────────


@dataclass
class FormationCandidate:
    topic: str
    """Canonical topic token (lowercased)."""

    recurrence_count: int
    source_memory_ids: list[str] = field(default_factory=list)
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    co_tokens: list[str] = field(default_factory=list)
    """Other tokens that frequently co-occur with the topic — gives the
    candidate a richer fingerprint than a single word."""

    def to_metadata(self) -> dict[str, Any]:
        return {
            "type": "goal_formation_candidate",
            "topic": self.topic,
            "recurrence_count": self.recurrence_count,
            "source_memory_ids": list(self.source_memory_ids),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "co_tokens": list(self.co_tokens),
            "cp1_provisional": True,
        }


# ── Pure helpers (no I/O) ────────────────────────────────────────────────────


def _tokenize(text: str, min_len: int = DEFAULT_MIN_TOKEN_LEN) -> list[str]:
    if not text:
        return []
    return [
        m.lower()
        for m in _TOKEN_RE.findall(text)
        if len(m) >= min_len and m.lower() not in _STOPWORDS
    ]


def detect_candidates(
    items: list[dict[str, Any]],
    *,
    min_recurrence: int = DEFAULT_MIN_RECURRENCE,
    min_token_len: int = DEFAULT_MIN_TOKEN_LEN,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> list[FormationCandidate]:
    """Pure function: given a list of {id, text, timestamp} dicts, return
    the strongest formation candidates (by recurrence count, then recency).

    `items` is sorted oldest → newest by caller. We do NOT touch I/O.
    """
    if not items:
        return []

    # token -> list of (item_idx, item_id, timestamp)
    occurrences: dict[str, list[tuple[int, str, str]]] = {}
    for idx, item in enumerate(items):
        tokens = set(_tokenize(item.get("text", ""), min_len=min_token_len))
        for tok in tokens:
            occurrences.setdefault(tok, []).append(
                (idx, str(item.get("id", "")), str(item.get("timestamp", "")))
            )

    candidates: list[FormationCandidate] = []
    for token, hits in occurrences.items():
        if len(hits) < min_recurrence:
            continue
        # Co-tokens: the most common other-tokens in the same items
        co_counter: dict[str, int] = {}
        hit_indices = {h[0] for h in hits}
        for hi in hit_indices:
            for ot in set(_tokenize(items[hi].get("text", ""))):
                if ot != token:
                    co_counter[ot] = co_counter.get(ot, 0) + 1
        co_top = sorted(co_counter.items(), key=lambda kv: (-kv[1], kv[0]))[:5]

        candidates.append(
            FormationCandidate(
                topic=token,
                recurrence_count=len(hits),
                source_memory_ids=[h[1] for h in hits if h[1]],
                first_seen=hits[0][2] or None,
                last_seen=hits[-1][2] or None,
                co_tokens=[t for t, _ in co_top],
            )
        )

    # Rank: recurrence_count desc, then last_seen desc (most recent wins ties)
    candidates.sort(
        key=lambda c: (-c.recurrence_count, c.last_seen or ""),
        reverse=False,
    )
    return candidates[:max_candidates]


# ── Cortex-aware scan (touches DB) ───────────────────────────────────────────


def _fetch_recent_items(cortex: "Cortex", lookback_days: int) -> list[dict[str, Any]]:
    """Pull recent EPISODIC memories + ring observations for the scan window.
    Returns a list sorted oldest → newest. Best-effort; logs and returns []
    on failure.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    items: list[dict[str, Any]] = []
    try:
        with cortex._db() as conn:
            conn.execute(
                "SELECT id, narrative, timestamp FROM memories "
                "WHERE memory_type = %s AND timestamp >= %s "
                "ORDER BY timestamp ASC LIMIT 500",
                ("EPISODIC", cutoff),
            )
            for row in conn.fetchall() or []:
                items.append(
                    {"id": row[0], "text": row[1] or "", "timestamp": row[2] or ""}
                )
    except Exception as exc:
        logger.warning("goal_formation _fetch_recent_items failed: %s", exc)
    return items


def scan_for_recurrence(
    cortex: "Cortex",
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    min_recurrence: int = DEFAULT_MIN_RECURRENCE,
    push_to_twm: bool = True,
) -> list[FormationCandidate]:
    """Run a recurrence detection pass. Returns candidates. If
    push_to_twm is True, the strongest candidate is pushed as a TWM marker
    at category='goal_formation_candidate'."""
    items = _fetch_recent_items(cortex, lookback_days)
    candidates = detect_candidates(items, min_recurrence=min_recurrence)
    if not candidates:
        return []

    if push_to_twm:
        top = candidates[0]
        try:
            cortex.twm_push(
                source="goal_formation",
                content_csb=(
                    f"GOAL_FORMATION_CANDIDATE topic={top.topic!r} "
                    f"recurrence={top.recurrence_count} "
                    f"co_tokens={top.co_tokens}"
                ),
                salience=0.55,
                metadata=top.to_metadata(),
                category="goal_formation_candidate",
            )
        except Exception as exc:
            logger.warning("goal_formation twm_push failed: %s", exc)
    return candidates
