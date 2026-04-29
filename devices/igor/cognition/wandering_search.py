"""
wandering_search.py — T-wandering-search

Word-ball / incremental browse layer over Igor's memories. Phenomenology
(Akien): Igor lands on a memory, "spins the ball" to explore neighbors,
can dwell on one and spin again from there. Closer to web-thesaurus
browsing than to one-shot ranked query.

MVP composition (this sprint):
  - seed_from_query(partial): pg_trgm trigram match on memories.narrative,
    returns top match as the wandering focus.
  - seed_from_memory(memory_id): set focus directly.
  - spin(top_k=8): pull neighbors of current focus from
    memories.links_weighted (explicit memory-to-memory edges, ranked by
    edge weight) and from trigram-similar narratives. Merge + dedup.
  - step(memory_id): move focus to a neighbor, append to trace.
  - twm_surface(): push current spin candidates to TWM with
    category="wandering" so other cognition surfaces can react.

Inertia: LOW — additive cognition module. Touches cortex via existing
public methods (_db, _to_memory, twm_push). New "wandering" TWM category
is just a string used at twm_push time; no dispatcher change.

Out of scope (follow-up tickets):
  - word_graph (wg_edges) walk for word-level semantic drift.
  - engram_id co-membership (lands when ensembles primitive ships).
  - Curiosity-driven autonomous wandering source (this is the deliberate
    interface; the autonomous push source comes later).
  - Dashboard/web UI surface.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from ..igor_base import IgorBase
from ..memory.models import Memory
from .forensic_logger import log_error
from ..igor_base import get_logger

if TYPE_CHECKING:
    from ..memory.cortex import Cortex

logger = get_logger(__name__)

DEFAULT_SPIN_TOP_K = 8
DEFAULT_SEED_LIMIT = 20
TRGM_QUERY_MAX_LEN = 60

_MEM_COLS = (
    "id, narrative, memory_type, parent_id, children_ids, link_ids, "
    "valence, arousal, dominance, activation_count, friction_history, "
    "timestamp, metadata, portable, links_weighted, last_accessed, source, "
    "confidence, context_of_encoding, updated_at, scope, payload"
)


def _parse_links_weighted(raw: object) -> dict[str, float]:
    """Coerce links_weighted (str-encoded JSON or dict) to {id: weight}."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): float(v) for k, v in raw.items() if v}
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return {str(k): float(v) for k, v in parsed.items() if v}
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.debug("wandering: links_weighted parse failed: %s", exc)
    return {}


class WanderingSearch(IgorBase):
    """Deliberate incremental browse over memories.

    Stateful: holds a current focus memory and a trace of visited memories.
    Igor invokes this directly (tool-style) when he wants to wander rather
    than query.
    """

    name: str = "wandering_search"

    def __init__(self, cortex: "Cortex") -> None:
        super().__init__()
        self.cortex = cortex
        self._focus: Optional[Memory] = None
        self._trace: list[str] = []

    @property
    def focus(self) -> Optional[Memory]:
        return self._focus

    @property
    def trace(self) -> list[str]:
        return list(self._trace)

    def seed_from_query(
        self, partial: str, limit: int = DEFAULT_SEED_LIMIT
    ) -> Optional[Memory]:
        """Substring-match `partial` against memories.narrative; set focus to top hit.

        Uses ILIKE for actual substring detection (the GIN trigram index on
        narrative makes this fast). similarity() ranks remaining candidates so
        the closest contextual match wins. Pure pg_trgm `%` is too strict for
        short-query-against-long-narrative — Google-presearch shape is
        substring-driven.
        """
        if not partial or len(partial) > TRGM_QUERY_MAX_LEN:
            return None
        pattern = f"%{partial}%"
        rows: list = []
        try:
            with self.cortex._db() as conn:
                conn.execute(
                    f"SELECT {_MEM_COLS} FROM memories "
                    "WHERE narrative ILIKE %s "
                    "ORDER BY similarity(narrative, %s) DESC "
                    "LIMIT %s",
                    (pattern, partial, limit),
                )
                rows = conn.fetchall() or []
        except Exception as exc:
            log_error(
                kind="WANDERING_SEARCH",
                detail=f"seed_from_query failed: {exc}",
            )
            return None

        memories = self._rows_to_memories(rows)
        if not memories:
            return None
        self._focus = memories[0]
        self._trace.append(self._focus.id)
        return self._focus

    def seed_from_memory(self, memory_id: str) -> Optional[Memory]:
        """Set focus to the named memory by id."""
        if not memory_id:
            return None
        try:
            mem = self.cortex.get(memory_id)
        except Exception as exc:
            log_error(
                kind="WANDERING_SEARCH",
                detail=f"seed_from_memory get failed: {exc}",
            )
            return None
        if mem is None:
            return None
        self._focus = mem
        self._trace.append(mem.id)
        return mem

    def spin(self, top_k: int = DEFAULT_SPIN_TOP_K) -> list[Memory]:
        """Return up to top_k neighbor memories of the current focus.

        Two-layer neighbor model:
          1. Explicit memory-to-memory links (links_weighted), ordered by weight.
          2. Trigram-similar narratives (semantic surface proximity).

        Layers are merged, deduplicated by id (focus excluded), capped at top_k.
        """
        if self._focus is None:
            return []

        link_neighbors = self._spin_via_links(self._focus, top_k)
        trgm_neighbors = self._spin_via_trigram(self._focus, top_k)

        seen: set[str] = {self._focus.id}
        merged: list[Memory] = []
        for layer in (link_neighbors, trgm_neighbors):
            for mem in layer:
                if mem.id in seen:
                    continue
                seen.add(mem.id)
                merged.append(mem)
                if len(merged) >= top_k:
                    return merged
        return merged

    def _spin_via_links(self, focus: Memory, top_k: int) -> list[Memory]:
        """Pull neighbors from focus.links_weighted ordered by edge weight."""
        weighted = _parse_links_weighted(getattr(focus, "links_weighted", None))
        if not weighted and focus.links:
            weighted = {str(k): float(v) for k, v in focus.links.items() if v}
        if not weighted:
            return []
        sorted_ids = [
            mid
            for mid, _ in sorted(weighted.items(), key=lambda kv: kv[1], reverse=True)
        ]
        out: list[Memory] = []
        for mid in sorted_ids[: top_k * 2]:
            try:
                mem = self.cortex.get(mid)
            except Exception as exc:
                logger.debug("wandering: link neighbor %s skipped: %s", mid, exc)
                continue
            if mem is not None:
                out.append(mem)
            if len(out) >= top_k:
                break
        return out

    def _spin_via_trigram(self, focus: Memory, top_k: int) -> list[Memory]:
        """Pull narrative-similar memories via pg_trgm similarity."""
        narrative = (focus.narrative or "")[:TRGM_QUERY_MAX_LEN]
        if not narrative:
            return []
        rows: list = []
        try:
            with self.cortex._db() as conn:
                conn.execute(
                    f"SELECT {_MEM_COLS} FROM memories "
                    "WHERE id <> %s AND narrative %% %s "
                    "ORDER BY similarity(narrative, %s) DESC "
                    "LIMIT %s",
                    (focus.id, narrative, narrative, top_k),
                )
                rows = conn.fetchall() or []
        except Exception as exc:
            log_error(
                kind="WANDERING_SEARCH",
                detail=f"_spin_via_trigram failed: {exc}",
            )
            return []
        return self._rows_to_memories(rows)

    def step(self, memory_id: str) -> Optional[Memory]:
        """Move focus to a neighbor; append to trace."""
        return self.seed_from_memory(memory_id)

    def twm_surface(self, neighbors: list[Memory]) -> list[int]:
        """Push current spin neighbors to TWM under category=wandering.

        Returns list of TWM ids. Salience is intentionally low — wandering
        is a deliberate browse, not an alert.
        """
        if not neighbors or self._focus is None:
            return []
        ts = datetime.now(timezone.utc).isoformat()
        ids: list[int] = []
        for mem in neighbors:
            try:
                excerpt = (mem.narrative or "")[:80].replace("\n", " ")
                twm_id = self.cortex.twm_push(
                    source="wandering_search",
                    content_csb=f"WANDERING|focus={self._focus.id}|near={mem.id}|{excerpt}",
                    salience=0.2,
                    urgency=0.0,
                    ttl_seconds=900,
                    category="wandering",
                    metadata={
                        "focus_id": self._focus.id,
                        "neighbor_id": mem.id,
                        "ts": ts,
                    },
                )
                if twm_id:
                    ids.append(twm_id)
            except Exception as exc:
                log_error(
                    kind="WANDERING_SEARCH",
                    detail=f"twm_surface for {mem.id}: {exc}",
                )
        return ids

    def reset(self) -> None:
        """Clear focus and trace."""
        self._focus = None
        self._trace = []

    def _rows_to_memories(self, rows: list) -> list[Memory]:
        """Convert raw rows via cortex._to_memory."""
        out: list[Memory] = []
        for r in rows:
            try:
                mem = self.cortex._to_memory(r)
            except Exception as exc:
                logger.debug("wandering: row skipped: %s", exc)
                continue
            out.append(mem)
        return out
