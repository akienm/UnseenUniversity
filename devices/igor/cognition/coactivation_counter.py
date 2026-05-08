"""
coactivation_counter.py — word-graph ↔ memory co-activation bridge.

Strengthens word-graph connections when queries and memories co-occur
during retrieval. NOT Hebbian pairwise edge weight updates — there are no
pairwise (A,B) co-activation matrices here. Functions in this module treat
co-occurrence as a scalar reinforcement signal applied to the word graph.

Was: hebbian_bridge.py (misnamed; back-compat shim retained at that path).

Three functions, all gated behind IGOR_HEBBIAN_BRIDGE env var (default off):

1. wg_boost_search(word_graph, query_text, candidates) -> dict[str, float]
   Word graph → memory search: predict_next() on query, boost candidates
   whose narratives contain predicted words.

2. record_retrieval_boost(word_graph, memory, arousal) -> None
   Memory → word graph: when an important node is retrieved, boost its
   key terms in the word graph proportional to arousal at retrieval time.

3. wg_predict_for_activation(word_graph, activated) -> set[str]
   Spreading activation extension: call predict_next() for each activated
   node's snippet, return union of predicted words for the caller.
"""

import logging
from ..igor_base import get_logger
import os

log = get_logger(__name__)
forensic = logging.getLogger("forensic")

_ENABLED = os.getenv("IGOR_HEBBIAN_BRIDGE", "false").lower() in ("1", "true", "yes")

# Score caps
_WG_BOOST_MAX = 0.10  # max per-candidate boost from wg predictions
_ARSL_BOOST_CAP = 0.15  # max per-retrieval wg reinforce (at arousal=1.0)
_QUERY_BOOST_BASE = 0.04  # T-learning-retrieval-signal: base boost for rank-1 result

# ── T-learning-retrieval-signal: global word graph ref ────────────────────────
# set_word_graph() is called from main.py after word graph is initialised.
# Allows cortex.search() to call reinforce_query_tokens() without needing the
# word_graph passed through every call site.
_wg_ref = None


def set_word_graph(word_graph) -> None:
    """Register the global word graph instance for retrieval-signal reinforcement."""
    global _wg_ref
    _wg_ref = word_graph


def get_word_graph():
    """Return the registered word graph, or None if not yet initialised."""
    return _wg_ref


def reinforce_query_tokens(query: str, results: list) -> None:
    """
    T-learning-retrieval-signal: reinforce query tokens proportional to 1/rank
    for each top result. Teaches the word graph: "these query words reliably
    retrieve high-quality content."

    Uses reinforce_text(query, boost) so all co-occurrence edges between query
    words get strengthened, plus reinforce(doc_id, boost) to strengthen the
    (word → doc) connections for the retrieved memory.

    Rank-1 result: boost = _QUERY_BOOST_BASE
    Rank-2:        boost = _QUERY_BOOST_BASE / 2
    Rank-3+:       boost = _QUERY_BOOST_BASE / rank  (diminishing)
    Cap at rank 3 to bound learning signal per search call.
    """
    if not _ENABLED or _wg_ref is None or not results or not query.strip():
        return
    try:
        for rank, mem in enumerate(results[:3], start=1):
            boost = _QUERY_BOOST_BASE / rank
            # Reinforce query-side co-occurrences
            _wg_ref.reinforce_text(query, boost=boost)
            # Reinforce doc connections for retrieved memory
            _doc_id = getattr(mem, "id", None)
            if _doc_id:
                _wg_ref.reinforce(_doc_id, boost=boost)
        forensic.debug(
            "[coactivation_counter] query_reinforce: query=%r top=%d results boosted",
            query[:40],
            min(3, len(results)),
        )
    except Exception as e:
        log.debug("[coactivation_counter] reinforce_query_tokens error: %s", e)


def wg_boost_search(
    word_graph,
    query_text: str,
    candidates: list,
) -> dict:
    """
    Part 1 — word graph → memory search bridge.

    Calls word_graph.predict_next(query_text, n=8) to get high-confidence
    next-word predictions for the query, then scores each candidate memory
    by how many predicted words appear in its narrative.

    Returns {memory_id: boost_delta}; boost in [0, WG_BOOST_MAX].
    Returns empty dict when disabled or word_graph is None.
    """
    if not _ENABLED or word_graph is None or not candidates:
        return {}

    try:
        predictions = word_graph.predict_next(query_text, n=8)
        if not predictions:
            return {}

        predicted_words = {w.lower() for w, _ in predictions}
        max_possible = len(predicted_words)
        if max_possible == 0:
            return {}

        boosts: dict[str, float] = {}
        for m in candidates:
            narrative_lower = (getattr(m, "narrative", None) or "").lower()
            hit_count = sum(1 for w in predicted_words if w in narrative_lower)
            if hit_count > 0:
                boost = min(_WG_BOOST_MAX, (hit_count / max_possible) * _WG_BOOST_MAX)
                boosts[m.id] = boost

        if boosts:
            forensic.debug(
                "[coactivation_counter] wg_boost: %d/%d candidates boosted from %d predictions",
                len(boosts),
                len(candidates),
                len(predictions),
            )
        return boosts

    except Exception as e:
        log.debug("[coactivation_counter] wg_boost_search error: %s", e)
        return {}


def record_retrieval_boost(word_graph, memory, arousal: float) -> None:
    """
    Part 2 — memory → word graph feedback.

    When a high-importance memory (importance >= 0.7) is retrieved,
    boost its key terms in the word graph proportional to current arousal.
    Max boost = arousal * _ARSL_BOOST_CAP (= 0.15 at arousal=1.0).

    No-op if disabled, word_graph is None, or importance < 0.7.
    """
    if not _ENABLED or word_graph is None or memory is None:
        return

    importance = getattr(memory, "importance", None) or getattr(
        memory, "confidence", 0.0
    )
    if (importance or 0.0) < 0.7:
        return

    try:
        narrative = getattr(memory, "narrative", None) or ""
        if not narrative.strip():
            return

        # Extract key terms: unique words longer than 3 chars (skip stopwords heuristic)
        words = narrative.split()
        unique = list(dict.fromkeys(w.lower() for w in words if len(w) > 3))
        key_terms = " ".join(unique[:15])
        if not key_terms:
            return

        boost = max(0.01, min(_ARSL_BOOST_CAP, float(arousal) * _ARSL_BOOST_CAP))
        word_graph.reinforce_text(key_terms, boost=boost)

        forensic.debug(
            "[coactivation_counter] retrieval_boost: memory=%s importance=%.2f"
            " arousal=%.2f boost=%.3f",
            memory.id,
            importance,
            arousal,
            boost,
        )

    except Exception as e:
        log.debug("[coactivation_counter] record_retrieval_boost error: %s", e)


def wg_predict_for_activation(word_graph, activated: list) -> set:
    """
    Part 3 — spreading activation word graph extension.

    For each activated memory (up to 5), call predict_next() on a short
    snippet of its narrative. Returns the union of all predicted words.

    Caller uses this set to widen candidate selection (e.g. scoring
    already-loaded memories by narrative overlap with predicted words).

    Returns empty set when disabled, word_graph is None, or no activations.
    """
    if not _ENABLED or word_graph is None or not activated:
        return set()

    try:
        predicted: set[str] = set()
        for m in activated[:5]:  # cap at 5 to bound latency
            snippet = (getattr(m, "narrative", None) or "")[:80]
            if not snippet.strip():
                continue
            preds = word_graph.predict_next(snippet, n=4)
            for w, _ in preds:
                predicted.add(w.lower())

        if predicted:
            forensic.debug(
                "[coactivation_counter] spread_wg: %d predicted words from %d activated nodes",
                len(predicted),
                min(len(activated), 5),
            )
        return predicted

    except Exception as e:
        log.debug("[coactivation_counter] wg_predict_for_activation error: %s", e)
        return set()
