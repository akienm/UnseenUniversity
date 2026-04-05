"""
Episodic Consolidation Daemon — #169.

Hippocampal replay analog: clusters similar EPISODIC memories, extracts
FACTUAL / INTERPRETIVE / PROCEDURAL patterns, stores them to LTM.

Design principles:
  - Never deletes source episodics (Discworld: repair, don't discard)
  - Uses local LLM only (Ollama tier.2 — no cloud cost)
  - Runs in background on schedule; checkpoint prevents re-processing
  - Target ratio: 10:1 → 2:1 episodic:interpretive over time

Trigger: called from main.py background drain after session idle.
Gate: IGOR_CONSOLIDATION_ENABLED (default true)
Schedule: no more than once per IGOR_CONSOLIDATION_INTERVAL_SECS (default 3600)
Batch: IGOR_CONSOLIDATION_BATCH (default 20 episodics per run)
"""

from __future__ import annotations
import logging

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..memory.cortex import Cortex, _MEM_COLS_NO_EMBED
from ..memory.models import Memory, MemoryType
from ..paths import paths

# ── Config ─────────────────────────────────────────────────────────────────────
_ENABLED = lambda: os.getenv("IGOR_CONSOLIDATION_ENABLED", "true").lower() == "true"
_INTERVAL_SECS = lambda: int(os.getenv("IGOR_CONSOLIDATION_INTERVAL_SECS", "3600"))
_BATCH_SIZE = lambda: int(os.getenv("IGOR_CONSOLIDATION_BATCH", "20"))
_MIN_IMPORTANCE = float(os.getenv("IGOR_CONSOLIDATION_MIN_IMPORTANCE", "0.5"))
_CHECKPOINT_FILE = paths().consolidation_checkpoint

_last_run: float = 0.0


# ── Checkpoint persistence ─────────────────────────────────────────────────────


def _load_checkpoint() -> dict:
    try:
        if _CHECKPOINT_FILE.exists():
            return json.loads(_CHECKPOINT_FILE.read_text())
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/cognition/consolidation.py: %s", _bare_e
        )
    return {"last_run_ts": 0.0, "processed_ids": []}


def _save_checkpoint(ts: float, processed_ids: list[str]) -> None:
    try:
        data = _load_checkpoint()
        data["last_run_ts"] = ts
        # Keep last 1000 processed IDs to avoid re-extracting same clusters
        data["processed_ids"] = (data.get("processed_ids", []) + processed_ids)[-1000:]
        _CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CHECKPOINT_FILE.write_text(json.dumps(data, indent=2))
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/cognition/consolidation.py: %s", _bare_e
        )


# ── Clustering ─────────────────────────────────────────────────────────────────


def _keyword_overlap(a: str, b: str) -> float:
    """Simple keyword Jaccard overlap between two strings."""
    _stop = {
        "the",
        "a",
        "an",
        "is",
        "was",
        "i",
        "and",
        "or",
        "but",
        "in",
        "on",
        "to",
        "of",
        "it",
    }
    ta = {w for w in a.lower().split() if len(w) > 3 and w not in _stop}
    tb = {w for w in b.lower().split() if len(w) > 3 and w not in _stop}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _cluster_episodics(
    memories: list[Memory], threshold: float = 0.15
) -> list[list[Memory]]:
    """
    Greedy single-pass clustering by keyword overlap.
    Each memory joins the first existing cluster it has ≥ threshold overlap with,
    or starts a new cluster. Keeps clusters to ≤ 8 members.
    """
    clusters: list[list[Memory]] = []
    for mem in memories:
        placed = False
        for cluster in clusters:
            if len(cluster) >= 8:
                continue
            rep = cluster[0]
            if _keyword_overlap(mem.narrative, rep.narrative) >= threshold:
                cluster.append(mem)
                placed = True
                break
        if not placed:
            clusters.append([mem])
    # Return only clusters with 2+ members — singletons have nothing to consolidate
    return [c for c in clusters if len(c) >= 2]


# ── LLM extraction ─────────────────────────────────────────────────────────────

_EXTRACTION_PROMPT = """\
You are Igor's memory consolidation system. Below are {n} related episodic memories.
Extract the most durable, reusable insight from this cluster.

EPISODICS:
{snippets}

Output a JSON object with these fields:
  "type": one of "FACTUAL" | "INTERPRETIVE" | "PROCEDURAL"
  "narrative": concise 1-2 sentence summary of the durable pattern (not a summary of events)
  "importance": 0.0-1.0 (how likely this pattern is to be useful again)
  "keywords": list of 3-5 key terms

Rules:
- FACTUAL: a stable fact about the world, Akien, or Igor (e.g. "Akien uses ADD medication")
- INTERPRETIVE: a meaning assignment (e.g. "When Akien goes quiet, he's usually mulling something")
- PROCEDURAL: a reusable procedure (e.g. "To restart Igor after schema change: use pause.wait")
- importance < 0.5 → skip (return null)

Respond with only the JSON object, or null if nothing worth keeping.
"""


def _call_local_llm(prompt: str, cortex: Cortex) -> Optional[dict]:
    """Call Ollama chat API directly to extract a consolidation candidate."""
    try:
        import ollama as _ollama
        import os as _os

        from .cluster_router import route as _route

        _host, _model = _route("extraction")
        if not _host:
            _host = _os.getenv("OLLAMA_HOST", "http://localhost:11434")
            _model = _os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b")
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
        # Strip markdown code fences if present
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


# ── Main consolidation pass ────────────────────────────────────────────────────


def run_consolidation(cortex: Cortex) -> dict:
    """
    Run one consolidation pass. Called from main.py background drain.
    Returns summary dict: {"clusters": N, "extracted": N, "skipped": N}
    """
    global _last_run

    if not _ENABLED():
        return {"skipped": "disabled"}

    now = time.time()
    if now - _last_run < _INTERVAL_SECS():
        return {"skipped": "too_soon"}

    checkpoint = _load_checkpoint()
    already_processed: set[str] = set(checkpoint.get("processed_ids", []))

    # Fetch recent EPISODIC memories not yet processed
    try:
        with cortex._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT {_MEM_COLS_NO_EMBED} FROM memories
                WHERE memory_type = 'EPISODIC'
                  AND id NOT IN ({{}})
                ORDER BY timestamp DESC
                LIMIT ?
                """.format(
                    ",".join("?" * len(already_processed))
                    if already_processed
                    else "'__none__'"
                ),
                (
                    list(already_processed) + [_BATCH_SIZE()]
                    if already_processed
                    else [_BATCH_SIZE()]
                ),
            ).fetchall()
        episodics = [cortex._to_memory(r) for r in rows if cortex._to_memory(r)]
    except Exception:
        return {"error": "fetch_failed"}

    if not episodics:
        _last_run = now
        return {"clusters": 0, "extracted": 0, "skipped": 0}

    clusters = _cluster_episodics(episodics)
    extracted = 0
    skipped = 0
    new_processed_ids: list[str] = [m.id for m in episodics]

    for cluster in clusters:
        snippets = "\n".join(
            f"  [{i+1}] {m.narrative[:400]}" for i, m in enumerate(cluster)
        )
        prompt = _EXTRACTION_PROMPT.format(n=len(cluster), snippets=snippets)
        result = _call_local_llm(prompt, cortex)

        if not result or result.get("importance", 0.0) < _MIN_IMPORTANCE:
            skipped += 1
            continue

        try:
            mtype_str = result.get("type", "FACTUAL").upper()
            mtype = {
                "FACTUAL": MemoryType.FACTUAL,
                "INTERPRETIVE": MemoryType.INTERPRETIVE,
                "PROCEDURAL": MemoryType.PROCEDURAL,
            }.get(mtype_str, MemoryType.FACTUAL)

            narrative = result.get("narrative", "").strip()
            if not narrative:
                skipped += 1
                continue

            importance = float(result.get("importance", 0.6))
            keywords = result.get("keywords", [])

            mem = cortex.store(
                narrative=narrative,
                memory_type=mtype,
                importance=importance,
                metadata={
                    "source": "consolidation",
                    "source_ids": [m.id for m in cluster],
                    "keywords": keywords,
                    "consolidated_at": datetime.utcnow().isoformat(),
                },
            )
            if mem:
                extracted += 1
        except Exception:
            skipped += 1

    # #180 NRE decay — run at same cadence as consolidation
    _decay_result = {}
    try:
        _decay_result = cortex.decay_investment_weights()
        if _decay_result.get("updated", 0) > 0:
            _ended = _decay_result.get("nre_ended", [])
            _note = f"|nre_ended={'|'.join(_ended)}" if _ended else ""
            cortex.write_ring(
                f"INVESTMENT_DECAY|updated={_decay_result['updated']}{_note}",
                category="investment_decay",
            )
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/cognition/consolidation.py: %s", _bare_e
        )

    _last_run = now
    _save_checkpoint(now, new_processed_ids)
    return {
        "clusters": len(clusters),
        "extracted": extracted,
        "skipped": skipped,
        "decay_updated": _decay_result.get("updated", 0),
    }


def should_run() -> bool:
    """Quick check: is it time for a consolidation pass?"""
    if not _ENABLED():
        return False
    checkpoint = _load_checkpoint()
    return time.time() - checkpoint.get("last_run_ts", 0.0) >= _INTERVAL_SECS()
