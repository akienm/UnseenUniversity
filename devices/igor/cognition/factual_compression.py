"""
factual_compression.py — FACTUAL→INTERPRETIVE compression pass.

Clusters thin FACTUAL deposits on the same concept and collapses them into
a single richer INTERPRETIVE node. Preserves book-fact provenance (book_title,
book_author, source_ids) on the output node.

Distinct from distillation.py (EPISODIC→EXPERIENTIAL→PROCEDURAL — temporal,
experiential learning). This pass is concept-spatial, not time-sequential.

Pass:
  1. Fetch unprocessed FACTUAL nodes (by checkpoint)
  2. Cluster by keyword Jaccard similarity (embedding cosine when available)
  3. For each cluster ≥ MIN_CLUSTER_SIZE:
     - Collect provenance (book_titles, source_ids)
     - Synthesize INTERPRETIVE narrative via local LLM
     - Novelty check against existing INTERPRETIVEs
     - Store INTERPRETIVE + activation edges to source FACTUALs
  4. Save checkpoint

Gates:
  IGOR_FACTUAL_COMPRESSION_ENABLED=true (default)
  IGOR_FACTUAL_COMPRESSION_INTERVAL_SECS=14400  (4h)
  IGOR_FACTUAL_COMPRESSION_BATCH=50
  IGOR_FACTUAL_COMPRESSION_SIM_THRESHOLD=0.20   (keyword Jaccard)
  IGOR_FACTUAL_COMPRESSION_NOVELTY_THRESHOLD=0.88

Ref: T-factual-compression, D259 discussion — Igor's spec: provenance-preserving,
separate module, cluster by concept.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

from ..memory.cortex import Cortex, _MEM_COLS_NO_EMBED
from ..memory.models import Memory, MemoryType
from ..paths import paths

log = logging.getLogger(__name__)
forensic = logging.getLogger("forensic")

# ── Config ─────────────────────────────────────────────────────────────────────

_ENABLED = (
    lambda: os.getenv("IGOR_FACTUAL_COMPRESSION_ENABLED", "true").lower() == "true"
)
_INTERVAL_SECS = lambda: int(
    os.getenv("IGOR_FACTUAL_COMPRESSION_INTERVAL_SECS", "14400")
)
_BATCH_SIZE = lambda: int(os.getenv("IGOR_FACTUAL_COMPRESSION_BATCH", "50"))
_SIM_THRESHOLD = float(os.getenv("IGOR_FACTUAL_COMPRESSION_SIM_THRESHOLD", "0.20"))
_NOVELTY_THRESHOLD = float(
    os.getenv("IGOR_FACTUAL_COMPRESSION_NOVELTY_THRESHOLD", "0.88")
)
_MIN_CLUSTER_SIZE = 2
_MAX_CLUSTER_SIZE = 8

_CHECKPOINT_FILE = paths().instance / "factual_compression_checkpoint.json"
_last_run: float = 0.0

_STOPWORDS = {
    "the",
    "and",
    "was",
    "that",
    "this",
    "with",
    "from",
    "have",
    "been",
    "for",
    "are",
    "but",
    "not",
    "you",
    "all",
    "can",
    "had",
    "her",
    "his",
}


# ── Checkpoint ─────────────────────────────────────────────────────────────────


def _load_checkpoint() -> dict:
    try:
        if _CHECKPOINT_FILE.exists():
            return json.loads(_CHECKPOINT_FILE.read_text())
    except Exception as _e:
        log.warning("factual_compression checkpoint read failed: %s", _e)
    return {"last_run_ts": 0.0, "processed_ids": []}


def _save_checkpoint(ts: float, processed_ids: list[str]) -> None:
    try:
        data = _load_checkpoint()
        data["last_run_ts"] = ts
        data["processed_ids"] = (data.get("processed_ids", []) + processed_ids)[-5000:]
        _CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CHECKPOINT_FILE.write_text(json.dumps(data, indent=2))
    except Exception as _e:
        log.warning("factual_compression checkpoint write failed: %s", _e)


# ── Keyword clustering ─────────────────────────────────────────────────────────


def _keywords(text: str) -> set[str]:
    """Non-stopword tokens longer than 3 chars."""
    return {w for w in text.lower().split() if len(w) > 3 and w not in _STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _cluster_factuals(memories: list[Memory]) -> list[list[Memory]]:
    """
    Cluster FACTUAL memories by keyword Jaccard similarity.
    Falls back gracefully — no external calls required.
    Returns only clusters with ≥ _MIN_CLUSTER_SIZE members.
    """
    # Try embedding-based clustering first
    try:
        from ..cognition.embedder import cosine_similarity
        from ..memory.cortex import Cortex as _C  # type: ignore

        # We don't have cortex here — skip embedding path, use keyword fallback
        raise ImportError("no cortex in scope")
    except Exception:
        pass

    # Keyword Jaccard clustering
    kw_cache = {m.id: _keywords(m.narrative) for m in memories}
    clusters: list[list[Memory]] = []

    for mem in memories:
        kw = kw_cache[mem.id]
        placed = False
        for cluster in clusters:
            if len(cluster) >= _MAX_CLUSTER_SIZE:
                continue
            rep_kw = kw_cache[cluster[0].id]
            if _jaccard(kw, rep_kw) >= _SIM_THRESHOLD:
                cluster.append(mem)
                placed = True
                break
        if not placed:
            clusters.append([mem])

    return [c for c in clusters if len(c) >= _MIN_CLUSTER_SIZE]


# ── Provenance extraction ──────────────────────────────────────────────────────


def _extract_provenance(cluster: list[Memory]) -> dict:
    """
    Collect provenance fields from a cluster of FACTUALs.
    Returns {source_ids, book_titles, book_authors, sources}.
    """
    source_ids = [m.id for m in cluster]
    book_titles: list[str] = []
    book_authors: list[str] = []
    sources: list[str] = []

    for m in cluster:
        meta = m.metadata or {}
        bt = meta.get("book_title", "")
        ba = meta.get("book_author", "")
        src = m.source or ""
        if bt and bt not in book_titles:
            book_titles.append(bt)
        if ba and ba not in book_authors:
            book_authors.append(ba)
        if src and src not in sources:
            sources.append(src)

    return {
        "source_ids": source_ids,
        "book_titles": book_titles,
        "book_authors": book_authors,
        "sources": sources,
    }


# ── LLM synthesis ─────────────────────────────────────────────────────────────


_COMPRESSION_PROMPT = """\
You are Igor's knowledge compression system. Below are {n} related factual observations \
from {source_desc}.

FACTS:
{snippets}

Synthesize these into a single INTERPRETIVE insight — a durable understanding of what \
these facts mean together, not a summary of each.

Output a JSON object:
  "narrative": 1-2 sentence insight capturing the shared concept (subject=Igor or general truth)
  "concept": 3-5 word label for this concept cluster
  "importance": 0.0-1.0

Rules:
- An INTERPRETIVE node captures meaning, not content
- Example: "Memory retrieval is modulated by emotional state — high arousal strengthens \
  episodic traces but narrows semantic search breadth."
- NOT a list of facts — a synthesized understanding
- importance < 0.4 → return null

Respond with only the JSON object, or null if nothing meaningful to synthesize.
"""


def _synthesize(cluster: list[Memory]) -> Optional[dict]:
    """Call local LLM to synthesize an INTERPRETIVE insight from a FACTUAL cluster."""
    snippets = "\n".join(
        f"  [{i+1}] {m.narrative[:180]}" for i, m in enumerate(cluster)
    )
    # Build source description for prompt context
    prov = _extract_provenance(cluster)
    titles = prov["book_titles"]
    if titles:
        source_desc = (
            f"{len(titles)} source(s): {', '.join(t[:60] for t in titles[:3])}"
        )
    else:
        source_desc = f"{len(cluster)} observations"

    prompt = _COMPRESSION_PROMPT.format(
        n=len(cluster), source_desc=source_desc, snippets=snippets
    )

    try:
        import ollama as _ollama
        from .inference_ollama import route as _route

        _host, _model = _route("extraction")
        if not _host:
            _host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
            _model = os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b")
        _client = _ollama.Client(host=_host)
        response = _client.chat(
            model=_model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1, "num_predict": 250},
        )
        raw = (
            response["message"]["content"]
            if isinstance(response, dict)
            else response.message.content
        )
        if not raw:
            return None
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1].strip()
            if raw.startswith("json"):
                raw = raw[4:].strip()
        if raw.lower() == "null" or not raw:
            return None
        return json.loads(raw)
    except Exception as _e:
        log.debug("factual_compression synthesis failed: %s", _e)
        return None


# ── Novelty check ──────────────────────────────────────────────────────────────


def _is_novel(narrative: str, cortex: Cortex) -> bool:
    """
    Return True if this narrative is sufficiently different from existing
    INTERPRETIVE nodes. Keyword-based when embeddings are unavailable.
    """
    try:
        from ..cognition.embedder import embed, cosine_similarity

        vec = embed(narrative)
        if vec is None:
            raise ValueError("no embedding")

        with cortex._conn() as conn:
            rows = conn.execute(
                f"SELECT {_MEM_COLS_NO_EMBED} FROM memories "
                "WHERE memory_type = %s "
                "ORDER BY activation_count DESC LIMIT 30",
                (MemoryType.INTERPRETIVE.value,),
            ).fetchall()

        existing = [cortex._to_memory(r) for r in rows]
        emb_map = cortex._get_embeddings_batch([m.id for m in existing])
        for m in existing:
            emb = emb_map.get(m.id) or cortex._get_or_compute_embedding(m)
            if emb and cosine_similarity(vec, emb) >= _NOVELTY_THRESHOLD:
                return False
        return True

    except Exception:
        # Keyword fallback
        kw_new = _keywords(narrative)
        try:
            with cortex._conn() as conn:
                rows = conn.execute(
                    f"SELECT narrative FROM memories "
                    "WHERE memory_type = %s LIMIT 50",
                    (MemoryType.INTERPRETIVE.value,),
                ).fetchall()
            for row in rows:
                if _jaccard(kw_new, _keywords(row[0])) >= 0.6:
                    return False
        except Exception:
            pass
        return True


# ── Main pass ──────────────────────────────────────────────────────────────────


def run_factual_compression(cortex: Cortex) -> dict:
    """
    Run one factual compression pass.
    Called from main.py background thread.
    Returns summary dict.
    """
    global _last_run

    if not _ENABLED():
        return {"skipped": "disabled"}

    now = time.time()
    if now - _last_run < _INTERVAL_SECS():
        return {"skipped": "too_soon"}

    checkpoint = _load_checkpoint()
    already_processed: set[str] = set(checkpoint.get("processed_ids", []))

    # Fetch unprocessed FACTUALs
    try:
        excl_clause = (
            f"AND id NOT IN ({','.join(['%s'] * len(already_processed))}) "
            if already_processed
            else ""
        )
        with cortex._conn() as conn:
            rows = conn.execute(
                f"SELECT {_MEM_COLS_NO_EMBED} FROM memories "
                f"WHERE memory_type = %s {excl_clause}"
                "ORDER BY timestamp DESC LIMIT %s",
                (
                    [MemoryType.FACTUAL.value]
                    + list(already_processed)
                    + [_BATCH_SIZE()]
                ),
            ).fetchall()
        factuals = [cortex._to_memory(r) for r in rows]
    except Exception as _e:
        log.warning("factual_compression fetch failed: %s", _e)
        return {"error": "fetch_failed"}

    new_processed_ids = [m.id for m in factuals]
    compressed = 0
    skipped = 0

    if factuals:
        clusters = _cluster_factuals(factuals)
        forensic.info(
            "[factual_compression] pass: %d factuals → %d clusters",
            len(factuals),
            len(clusters),
        )

        for cluster in clusters:
            prov = _extract_provenance(cluster)
            result = _synthesize(cluster)

            if not result or float(result.get("importance", 0.0)) < 0.4:
                skipped += 1
                continue

            narrative = (result.get("narrative") or "").strip()
            concept = (result.get("concept") or "").strip()
            if not narrative:
                skipped += 1
                continue

            if not _is_novel(narrative, cortex):
                forensic.debug(
                    "[factual_compression] skipped non-novel: %s…", narrative[:60]
                )
                skipped += 1
                continue

            try:
                importance = min(1.0, max(0.0, float(result.get("importance", 0.6))))
                from ..memory.node_id import new_node_id

                interp_id = new_node_id()
                interp = Memory(
                    id=interp_id,
                    narrative=narrative,
                    memory_type=MemoryType.INTERPRETIVE,
                    source="factual_compression",
                )
                interp.metadata = {
                    "source": "factual_compression",
                    "concept": concept,
                    "importance": importance,
                    "compressed_from": len(cluster),
                    "compressed_at": datetime.now(timezone.utc).isoformat(),
                    **prov,  # source_ids, book_titles, book_authors, sources
                }
                cortex.store(interp)

                # Link source FACTUALs to new INTERPRETIVE
                for src_mem in cluster:
                    cortex.add_interpretive_edge(
                        from_id=interp_id,
                        to_id=src_mem.id,
                        direction="activation",
                        weight=importance,
                        layer="compression",
                    )

                forensic.info(
                    "[factual_compression] stored INTERPRETIVE %s concept=%r "
                    "sources=%d books=%s",
                    interp_id,
                    concept,
                    len(cluster),
                    prov["book_titles"][:2],
                )
                compressed += 1

            except Exception as _e:
                log.warning("factual_compression store failed: %s", _e)
                skipped += 1

    _last_run = now
    _save_checkpoint(now, new_processed_ids)

    summary = {
        "factuals_reviewed": len(factuals),
        "clusters_found": len(_cluster_factuals(factuals)) if factuals else 0,
        "compressed": compressed,
        "skipped": skipped,
    }
    forensic.info("[factual_compression] pass complete: %s", summary)
    return summary


def should_run() -> bool:
    """Quick check: is it time for a compression pass?"""
    if not _ENABLED():
        return False
    checkpoint = _load_checkpoint()
    return time.time() - checkpoint.get("last_run_ts", 0.0) >= _INTERVAL_SECS()
