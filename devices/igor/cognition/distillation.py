"""
distillation.py — T-distillation-daemon: EPISODIC → EXPERIENTIAL → PROCEDURAL lifecycle.

Two-pass metabolism fix:

Pass 1 — DISTILLATION:
  Clusters EPISODIC nodes by embedding similarity (cosine ≥ threshold; keyword fallback
  when Ollama unavailable). For each cluster, calls local LLM to synthesize a candidate
  EXPERIENTIAL node. Novelty check: skip if too similar to an existing EXPERIENTIAL node
  (cosine ≥ NOVELTY_THRESHOLD). Stores novel nodes as EXPERIENTIAL children of CP1.

Pass 2 — GRADUATION:
  Scans EXPERIENTIAL nodes with activation_count ≥ GRADUATION_THRESHOLD. For each,
  proposes a PROCEDURAL memory (same narrative, type changed, source="distillation_grad").
  Only graduates if no PROCEDURAL with similar content already exists.

Differences from consolidation.py:
  - consolidation.py: keyword clustering → FACTUAL/INTERPRETIVE/PROCEDURAL
  - distillation.py: embedding clustering → EXPERIENTIAL (pass 1)
                      activation threshold → PROCEDURAL proposal (pass 2)

Gates:
  IGOR_DISTILLATION_ENABLED=true (default)
  IGOR_DISTILLATION_INTERVAL_SECS=7200
  IGOR_DISTILLATION_BATCH=30
  IGOR_DISTILLATION_EMBED_THRESHOLD=0.70
  IGOR_DISTILLATION_NOVELTY_THRESHOLD=0.90
  IGOR_DISTILLATION_GRADUATION_THRESHOLD=15

Run from main.py: _run_distillation_background() mirrors _run_consolidation_background().
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

_ENABLED = lambda: os.getenv("IGOR_DISTILLATION_ENABLED", "true").lower() == "true"
_INTERVAL_SECS = lambda: int(os.getenv("IGOR_DISTILLATION_INTERVAL_SECS", "7200"))
_BATCH_SIZE = lambda: int(os.getenv("IGOR_DISTILLATION_BATCH", "30"))
_EMBED_THRESHOLD = float(os.getenv("IGOR_DISTILLATION_EMBED_THRESHOLD", "0.70"))
_NOVELTY_THRESHOLD = float(os.getenv("IGOR_DISTILLATION_NOVELTY_THRESHOLD", "0.90"))
_GRADUATION_THRESHOLD = int(os.getenv("IGOR_DISTILLATION_GRADUATION_THRESHOLD", "15"))
_MIN_CLUSTER_SIZE = 2

_CHECKPOINT_FILE = paths().instance / "distillation_checkpoint.json"

_last_run: float = 0.0


# ── Checkpoint ─────────────────────────────────────────────────────────────────


def _load_checkpoint() -> dict:
    try:
        if _CHECKPOINT_FILE.exists():
            return json.loads(_CHECKPOINT_FILE.read_text())
    except Exception as _bare_e:
        log.warning(
            "bare except in wild_igor/igor/cognition/distillation.py: %s", _bare_e
        )
    return {"last_run_ts": 0.0, "processed_ids": []}


def _save_checkpoint(ts: float, processed_ids: list[str]) -> None:
    try:
        data = _load_checkpoint()
        data["last_run_ts"] = ts
        data["processed_ids"] = (data.get("processed_ids", []) + processed_ids)[-2000:]
        _CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CHECKPOINT_FILE.write_text(json.dumps(data, indent=2))
    except Exception as _bare_e:
        log.warning(
            "bare except in wild_igor/igor/cognition/distillation.py: %s", _bare_e
        )


# ── Embedding clustering ────────────────────────────────────────────────────────


def _keyword_overlap(a: str, b: str) -> float:
    """Jaccard overlap on meaningful words (len > 3, not stopword)."""
    _stop = {"the", "and", "was", "that", "this", "with", "from", "have", "been"}
    ta = {w for w in a.lower().split() if len(w) > 3 and w not in _stop}
    tb = {w for w in b.lower().split() if len(w) > 3 and w not in _stop}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _cluster_by_embeddings(
    memories: list[Memory],
    cortex: Cortex,
    threshold: float,
) -> list[list[Memory]]:
    """
    Cluster memories by cosine similarity of their embeddings.
    Falls back to keyword overlap when embeddings are unavailable.
    Returns only clusters with ≥ _MIN_CLUSTER_SIZE members.
    """
    try:
        from ..cognition.embedder import cosine_similarity

        # Batch-fetch stored embeddings; compute missing ones
        emb_map: dict[str, Optional[list[float]]] = cortex._get_embeddings_batch(
            [m.id for m in memories]
        )
        for m in memories:
            if m.id not in emb_map or emb_map[m.id] is None:
                emb_map[m.id] = cortex._get_or_compute_embedding(m)

        embedding_available = any(v is not None for v in emb_map.values())

        if embedding_available:
            clusters: list[list[Memory]] = []
            for mem in memories:
                vec = emb_map.get(mem.id)
                if vec is None:
                    continue
                placed = False
                for cluster in clusters:
                    if len(cluster) >= 10:
                        continue
                    rep_vec = emb_map.get(cluster[0].id)
                    if rep_vec is not None:
                        sim = cosine_similarity(vec, rep_vec)
                        if sim >= threshold:
                            cluster.append(mem)
                            placed = True
                            break
                if not placed:
                    clusters.append([mem])
            return [c for c in clusters if len(c) >= _MIN_CLUSTER_SIZE]

    except Exception as _bare_e:
        log.debug("embedding cluster failed, falling back to keyword: %s", _bare_e)

    # Keyword fallback
    clusters2: list[list[Memory]] = []
    for mem in memories:
        placed = False
        for cluster in clusters2:
            if len(cluster) >= 8:
                continue
            if _keyword_overlap(mem.narrative, cluster[0].narrative) >= 0.15:
                cluster.append(mem)
                placed = True
                break
        if not placed:
            clusters2.append([mem])
    return [c for c in clusters2 if len(c) >= _MIN_CLUSTER_SIZE]


# ── Novelty check ───────────────────────────────────────────────────────────────


def _is_novel(
    narrative: str,
    cortex: Cortex,
    against_type: MemoryType,
    threshold: float,
) -> bool:
    """
    Return True if `narrative` is sufficiently different from existing nodes
    of `against_type`. Uses cosine similarity; falls back to True when
    embeddings are unavailable (assume novel, let dedup handle later).
    """
    try:
        from ..cognition.embedder import embed, cosine_similarity

        vec = embed(narrative)
        if vec is None:
            return True  # can't check — assume novel

        with cortex._conn() as conn:
            rows = conn.execute(
                f"SELECT {_MEM_COLS_NO_EMBED} FROM memories "
                "WHERE memory_type = ? "
                "ORDER BY activation_count DESC LIMIT 50",
                (against_type.value,),
            ).fetchall()

        existing = [cortex._to_memory(r) for r in rows]
        emb_map = cortex._get_embeddings_batch([m.id for m in existing])

        for m in existing:
            emb = emb_map.get(m.id)
            if emb is None:
                emb = cortex._get_or_compute_embedding(m)
            if emb is not None:
                if cosine_similarity(vec, emb) >= threshold:
                    return False  # too similar to existing node

        return True

    except Exception as _bare_e:
        log.debug("novelty check failed, assuming novel: %s", _bare_e)
        return True


# ── LLM extraction ─────────────────────────────────────────────────────────────


_DISTILLATION_PROMPT = """\
You are Igor's distillation system. Below are {n} related episodic memories.
Synthesize the durable experiential pattern they share.

EPISODICS:
{snippets}

Output a JSON object with these fields:
  "narrative": concise 1-2 sentence summary of the experiential pattern
               (what class of experience this represents, not a summary of events)
  "importance": 0.0-1.0 (how significant this pattern is for self-knowledge)
  "keywords": list of 3-5 key terms

Rules:
- An EXPERIENTIAL node captures a recurring category of emotional/cognitive experience
- Example: "When Akien is energized by a technical breakthrough, the conversation accelerates"
- NOT a compressed event summary — a generalized experiential truth
- importance < 0.5 → return null (nothing worth keeping)

Respond with only the JSON object, or null if nothing worth keeping.
"""


def _call_local_llm(prompt: str) -> Optional[dict]:
    """Call Ollama to synthesize a distillation candidate."""
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
            options={"temperature": 0.1, "num_predict": 300},
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
    except Exception:
        return None


# ── Pass 2: EXPERIENTIAL → PROCEDURAL graduation ───────────────────────────────


def _run_graduation_pass(cortex: Cortex) -> int:
    """
    Pass 2: EXPERIENTIAL nodes with activation_count >= threshold graduate
    to PROCEDURAL. Only inserts if no similar PROCEDURAL already exists.
    Returns count of graduations.
    """
    graduated = 0
    try:
        with cortex._conn() as conn:
            rows = conn.execute(
                f"SELECT {_MEM_COLS_NO_EMBED} FROM memories "
                "WHERE memory_type = ? AND activation_count >= ? "
                "ORDER BY activation_count DESC LIMIT 20",
                (MemoryType.EXPERIENTIAL.value, _GRADUATION_THRESHOLD),
            ).fetchall()

        candidates = [cortex._to_memory(r) for r in rows]

        for exp_mem in candidates:
            # Skip if already has a procedural child (check metadata)
            if exp_mem.metadata.get("graduated_to"):
                continue

            if not _is_novel(
                exp_mem.narrative, cortex, MemoryType.PROCEDURAL, _NOVELTY_THRESHOLD
            ):
                continue

            proc_id = f"PROC_DISTILL_{exp_mem.id[:8].upper()}"
            proc = Memory(
                id=proc_id,
                narrative=exp_mem.narrative,
                memory_type=MemoryType.PROCEDURAL,
            )
            proc.parent_id = "CP1"
            proc.metadata = {
                "source": "distillation_grad",
                "source_experiential_id": exp_mem.id,
                "graduated_at": datetime.now(timezone.utc).isoformat(),
            }
            cortex.store(proc)
            cortex.add_child("CP1", proc_id)

            # Mark source EXPERIENTIAL as graduated
            exp_mem.metadata["graduated_to"] = proc_id
            cortex.store(exp_mem)

            forensic.info(
                "[distillation] graduated EXPERIENTIAL %s → PROCEDURAL %s",
                exp_mem.id,
                proc_id,
            )
            graduated += 1

    except Exception as _bare_e:
        log.warning(
            "bare except in wild_igor/igor/cognition/distillation.py graduation: %s",
            _bare_e,
        )

    return graduated


# ── Main distillation pass ─────────────────────────────────────────────────────


def run_distillation(cortex: Cortex) -> dict:
    """
    Run one distillation pass (Pass 1 + Pass 2).
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

    # Fetch recent EPISODICs not yet processed
    try:
        with cortex._conn() as conn:
            rows = conn.execute(
                f"SELECT {_MEM_COLS_NO_EMBED} FROM memories "
                "WHERE memory_type = ? "
                + (
                    f"AND id NOT IN ({','.join('?' * len(already_processed))}) "
                    if already_processed
                    else ""
                )
                + "ORDER BY timestamp DESC LIMIT ?",
                (
                    [MemoryType.EPISODIC.value]
                    + list(already_processed)
                    + [_BATCH_SIZE()]
                ),
            ).fetchall()
        episodics = [cortex._to_memory(r) for r in rows]
    except Exception as _bare_e:
        log.warning(
            "bare except in wild_igor/igor/cognition/distillation.py fetch: %s",
            _bare_e,
        )
        return {"error": "fetch_failed"}

    new_processed_ids = [m.id for m in episodics]
    extracted = 0
    skipped = 0

    if episodics:
        clusters = _cluster_by_embeddings(episodics, cortex, _EMBED_THRESHOLD)

        forensic.info(
            "[distillation] pass1: %d episodics → %d clusters",
            len(episodics),
            len(clusters),
        )

        for cluster in clusters:
            snippets = "\n".join(
                f"  [{i+1}] {m.narrative[:200]}" for i, m in enumerate(cluster)
            )
            prompt = _DISTILLATION_PROMPT.format(n=len(cluster), snippets=snippets)
            result = _call_local_llm(prompt)

            if not result or float(result.get("importance", 0.0)) < 0.5:
                skipped += 1
                continue

            narrative = (result.get("narrative") or "").strip()
            if not narrative:
                skipped += 1
                continue

            if not _is_novel(
                narrative, cortex, MemoryType.EXPERIENTIAL, _NOVELTY_THRESHOLD
            ):
                forensic.debug(
                    "[distillation] skipped non-novel synthesis: %s…", narrative[:60]
                )
                skipped += 1
                continue

            try:
                importance = min(1.0, max(0.0, float(result.get("importance", 0.6))))
                keywords = result.get("keywords", [])

                exp_id = f"EXP_{int(now)}_{extracted}"
                exp = Memory(
                    id=exp_id,
                    narrative=narrative,
                    memory_type=MemoryType.EXPERIENTIAL,
                )
                exp.parent_id = "CP1"
                exp.metadata = {
                    "source": "distillation",
                    "source_ids": [m.id for m in cluster],
                    "keywords": keywords,
                    "distilled_at": datetime.now(timezone.utc).isoformat(),
                    "importance": importance,
                }
                cortex.store(exp)
                cortex.add_child("CP1", exp_id)

                forensic.info(
                    "[distillation] stored EXPERIENTIAL %s: %s…",
                    exp_id,
                    narrative[:60],
                )
                extracted += 1

            except Exception as _bare_e:
                log.warning(
                    "bare except in wild_igor/igor/cognition/distillation.py store: %s",
                    _bare_e,
                )
                skipped += 1

    # Pass 2: graduation
    graduated = _run_graduation_pass(cortex)

    _last_run = now
    _save_checkpoint(now, new_processed_ids)

    result_summary = {
        "episodics_reviewed": len(episodics),
        "clusters": (
            len(_cluster_by_embeddings(episodics, cortex, _EMBED_THRESHOLD))
            if episodics
            else 0
        ),
        "extracted": extracted,
        "skipped": skipped,
        "graduated": graduated,
    }
    forensic.info("[distillation] pass complete: %s", result_summary)
    return result_summary


def should_run() -> bool:
    """Quick check: is it time for a distillation pass?"""
    if not _ENABLED():
        return False
    checkpoint = _load_checkpoint()
    return time.time() - checkpoint.get("last_run_ts", 0.0) >= _INTERVAL_SECS()
