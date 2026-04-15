"""
search_widen.py — T-retrieval-widen-on-miss

When cortex.search() returns empty, try a sequence of loosened retrieval
passes before giving up. Biomimetic pattern: exact match fails → widen
the frame, don't dead-end.

## Why this exists

From Akien (2026-04-15) after Igor failed to find the 'igor dev facia
tree' because the canonical name is 'The Igors Project'. The bug wasn't
a name drift — it was the missing *widen on miss* reflex.

cortex.search already does Phase 0 traversal + Phase 1 text scoring +
Phase 2 embedding re-rank + Hebbian bridge. Those bridges broaden FROM
query tokens when there's something to anchor on. The hole is when the
first pass returns zero and there's no anchor to spread from.

## Strategies (tried in order, first non-empty wins)

1. **Token-loosened LIKE** — split query into tokens, run per-token
   ILIKE against narrative + display_name metadata. Bridges
   'igor dev' → 'The Igors Project' because `%igor%` matches.

2. **Word-graph neighbor expansion** — for each query token, pull
   co-occurring neighbors from the word graph, retry per-token LIKE
   with the expanded token set. Bridges 'dev' ↔ 'project' via
   learned co-occurrence.

3. **pg_trgm similarity** — when available, trigram similarity on
   narrative + display_name for the whole query. Catches typos and
   partial matches ('igodev' → 'igor dev'). Skipped silently if the
   extension isn't installed.

Each strategy returns Memory objects with `widened_from_empty=True`
set at runtime so callers can tell these apart from exact matches.

CP grounding:
- **CP1** — empty-after-widen is a valid result, not a failure
- **CP2** — when widen returns something the first pass missed, that's
  signal about where our retrieval priors are off
- **CP6** — widen never mutates persistent state; the flag is set on
  the Memory instance at runtime, not written back to the DB
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .cortex import Cortex
    from .models import Memory

logger = logging.getLogger(__name__)


MIN_TOKEN_LEN: int = 4
DEFAULT_WIDEN_LIMIT: int = 20
NEIGHBOR_EXPAND_PER_TOKEN: int = 5


_STOP_TOKENS: frozenset[str] = frozenset(
    {
        "what",
        "where",
        "when",
        "which",
        "whose",
        "would",
        "could",
        "should",
        "there",
        "their",
        "this",
        "that",
        "these",
        "those",
        "about",
        "from",
        "with",
        "into",
        "onto",
        "over",
        "under",
        "them",
        "they",
        "your",
        "yours",
        "mine",
        "ours",
        "also",
        "just",
        "like",
        "make",
        "made",
        "know",
        "knew",
        "think",
        "find",
        "tell",
        "told",
        "want",
        "need",
        "does",
        "didn",
    }
)


def _clean_tokens(query: str, min_len: int = MIN_TOKEN_LEN) -> list[str]:
    """Extract content tokens from a query string."""
    if not query:
        return []
    out: list[str] = []
    for raw in query.lower().split():
        tok = "".join(c for c in raw if c.isalnum() or c == "-" or c == "_")
        if len(tok) >= min_len and tok not in _STOP_TOKENS:
            out.append(tok)
    # De-dupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def _rows_to_memories(cortex: "Cortex", rows: list) -> list["Memory"]:
    """Convert raw rows via cortex._to_memory, mark as widened."""
    out: list[Memory] = []
    for r in rows:
        try:
            mem = cortex._to_memory(r)
        except Exception as exc:
            logger.debug("search_widen _to_memory skipped row: %s", exc)
            continue
        setattr(mem, "widened_from_empty", True)
        out.append(mem)
    return out


# ── Strategy 1: token-loosened LIKE ──────────────────────────────────────────


def _token_like_strategy(
    cortex: "Cortex", tokens: list[str], limit: int
) -> list["Memory"]:
    """Per-token ILIKE on narrative + display_name. First non-empty
    token wins (by design — we want *any* bridge, not a join)."""
    if not tokens:
        return []
    seen_ids: set[str] = set()
    out: list[Memory] = []
    for tok in tokens:
        pattern = f"%{tok}%"
        try:
            with cortex._db() as conn:
                conn.execute(
                    "SELECT id, narrative, memory_type, parent_id, "
                    "children_ids, link_ids, valence, arousal, dominance, "
                    "activation_count, friction_history, timestamp, metadata, "
                    "portable, links_weighted, last_accessed, source, "
                    "confidence, context_of_encoding, updated_at, scope, payload "
                    "FROM memories "
                    "WHERE (narrative ILIKE %s "
                    "   OR metadata->>'display_name' ILIKE %s) "
                    "  AND memory_type NOT IN ('ROOT', 'CORE_PATTERN') "
                    # Biomimetic ordering: structural anchors first. A widen-on-miss
                    # fallback should prefer named/typed things (facia, goals, tool
                    # registry) over individual activity memories that happen to
                    # contain the token. Activation_count breaks ties.
                    "ORDER BY "
                    "  (CASE WHEN jsonb_exists(metadata, 'facia_role') THEN 2 "
                    "        WHEN jsonb_exists(metadata, 'display_name') THEN 1 "
                    "        ELSE 0 END) DESC, "
                    "  activation_count DESC "
                    "LIMIT %s",
                    (pattern, pattern, limit),
                )
                rows = conn.fetchall() or []
        except Exception as exc:
            logger.warning("search_widen token_like failed for %r: %s", tok, exc)
            continue
        mems = _rows_to_memories(cortex, rows)
        for m in mems:
            if m.id not in seen_ids:
                seen_ids.add(m.id)
                out.append(m)
        if out:
            return out[:limit]
    return out[:limit]


# ── Strategy 2: word-graph neighbor expansion ────────────────────────────────


def _expand_via_word_graph(
    word_graph: Any, tokens: list[str], per_token: int = NEIGHBOR_EXPAND_PER_TOKEN
) -> list[str]:
    """Return the token set expanded with word-graph neighbors."""
    if word_graph is None:
        return list(tokens)
    expanded: list[str] = list(tokens)
    seen: set[str] = set(tokens)
    for tok in tokens:
        try:
            neighbors = word_graph.neighbors(tok, limit=per_token)
        except Exception as exc:
            logger.debug(
                "search_widen word_graph neighbors for %r failed: %s", tok, exc
            )
            continue
        for n in neighbors or []:
            n_str = n if isinstance(n, str) else getattr(n, "word", None) or str(n)
            n_clean = n_str.lower().strip()
            if n_clean and n_clean not in seen and len(n_clean) >= MIN_TOKEN_LEN:
                seen.add(n_clean)
                expanded.append(n_clean)
    return expanded


def _wg_neighbor_strategy(
    cortex: "Cortex", tokens: list[str], word_graph: Any, limit: int
) -> list["Memory"]:
    if word_graph is None or not tokens:
        return []
    expanded = _expand_via_word_graph(word_graph, tokens)
    if expanded == tokens:
        return []
    # Use only the new tokens (exclude the originals — those already failed)
    new_tokens = [t for t in expanded if t not in tokens]
    return _token_like_strategy(cortex, new_tokens, limit)


# ── Strategy 3: pg_trgm similarity ───────────────────────────────────────────


def _has_pg_trgm(cortex: "Cortex") -> bool:
    try:
        with cortex._db() as conn:
            conn.execute(
                "SELECT 1 FROM pg_extension WHERE extname = %s",
                ("pg_trgm",),
            )
            return conn.fetchone() is not None
    except Exception as exc:
        logger.debug("search_widen pg_trgm probe failed: %s", exc)
        return False


def _trgm_strategy(cortex: "Cortex", query: str, limit: int) -> list["Memory"]:
    if not query or len(query) > 60:
        return []
    if not _has_pg_trgm(cortex):
        return []
    try:
        with cortex._db() as conn:
            conn.execute(
                "SELECT id, narrative, memory_type, parent_id, "
                "children_ids, link_ids, valence, arousal, dominance, "
                "activation_count, friction_history, timestamp, metadata, "
                "portable, links_weighted, last_accessed, source, "
                "confidence, context_of_encoding, updated_at, scope, payload "
                "FROM memories "
                "WHERE memory_type NOT IN ('ROOT', 'CORE_PATTERN') "
                "  AND (narrative %% %s OR metadata->>'display_name' %% %s) "
                "ORDER BY similarity("
                "    narrative || ' ' || COALESCE(metadata->>'display_name', ''), "
                "    %s"
                ") DESC "
                "LIMIT %s",
                (query, query, query, limit),
            )
            rows = conn.fetchall() or []
    except Exception as exc:
        logger.debug("search_widen trgm strategy failed: %s", exc)
        return []
    return _rows_to_memories(cortex, rows)


# ── Orchestration ────────────────────────────────────────────────────────────


def widen_search(
    cortex: "Cortex",
    query: str,
    *,
    word_graph: Any = None,
    limit: int = DEFAULT_WIDEN_LIMIT,
    push_to_twm: bool = True,
) -> tuple[list["Memory"], Optional[str]]:
    """Run widen-on-miss strategies in order. Returns (results, strategy_name).

    strategy_name is one of 'token_like', 'wg_neighbor', 'pg_trgm', or
    None if nothing fired. Results carry widened_from_empty=True.
    """
    tokens = _clean_tokens(query)

    results: list[Memory] = []
    strategy_used: Optional[str] = None

    results = _token_like_strategy(cortex, tokens, limit)
    if results:
        strategy_used = "token_like"

    if not results and word_graph is not None:
        results = _wg_neighbor_strategy(cortex, tokens, word_graph, limit)
        if results:
            strategy_used = "wg_neighbor"

    if not results:
        results = _trgm_strategy(cortex, query, limit)
        if results:
            strategy_used = "pg_trgm"

    if push_to_twm and strategy_used:
        try:
            cortex.twm_push(
                source="search_widen",
                content_csb=(
                    f"WIDEN_ATTEMPT query={query!r} "
                    f"strategy={strategy_used} result_count={len(results)}"
                ),
                salience=0.4,
                metadata={
                    "type": "widen_attempt",
                    "original_query": query,
                    "strategy": strategy_used,
                    "result_count": len(results),
                    "result_ids": [m.id for m in results[:5]],
                },
                category="widen_attempt",
            )
        except Exception as exc:
            logger.debug("search_widen twm_push failed: %s", exc)

    return results, strategy_used
