"""consolidation.py — Episodic consolidation daemon (hippocampal replay analog).

WHAT IT IS
──────────
Consolidation clusters similar EPISODIC memories, extracts their shared
patterns (FACTUAL, INTERPRETIVE, PROCEDURAL), and stores durable insights
to LTM. It runs as a background daemon during session idle periods. No
episodic memories are ever deleted — consolidation produces derivatives
(Discworld principle: repair, don't discard).

WHY IT EXISTS
─────────────
Without consolidation, Igor's episodic buffer grows unbounded and later
memory queries become noise. Target ratio: 10:1 → 2:1 episodic:
interpretive over time. Consolidation is the hippocampal-cortical loop
transplanted into code: pick up fragmented experience during quiet
periods, extract meaning, and thread that meaning into permanent
knowledge structures. Graph stays lean; nothing is lost.

HOW IT WORKS (architecture)
───────────────────────────
Four-step pipeline:

1. Clustering (keyword overlap)
   Given a batch of recent EPISODIC memories (default 200), group by
   keyword Jaccard similarity (threshold 0.15). Clusters must have ≥ 2
   members (singletons have nothing to consolidate); max 8 members to
   keep prompts tractable. Output: topical units ripe for extraction.

2. Local LLM extraction (qwen2.5:7b via Ollama)
   For each cluster, assemble a prompt with cluster members (≤ 400 chars
   each) and ask: "Extract the durable pattern that unifies these
   experiences." LLM returns JSON:
     type       FACTUAL | INTERPRETIVE | PROCEDURAL
     narrative  1-2 sentence summary of the pattern, not the events
     importance 0.0–1.0
     keywords   extracted list
   Uses qwen2.5:7b exclusively (D360 benchmark: only model producing
   valid JSON extraction; DeepSeek 7B and llama 1B fail). No cloud cost.

3. Filtering by importance
   Skip extracted patterns where importance < MIN_IMPORTANCE (default
   0.4). Gates noise: "Weather was nice" fails; "Akien avoids Monday
   meetings when tired" passes.

4. Storage to cortex
   For patterns that pass filtering, call cortex.store() with:
     narrative    the extracted pattern
     memory_type  FACTUAL, INTERPRETIVE, or PROCEDURAL
     importance   learned score
     metadata     provenance — source cluster IDs, consolidated_at
                  timestamp, extracted keywords — so the pattern can be
                  traced back to its episodics.

ENGAGEMENT WITH CORTEX STATE
────────────────────────────
Consolidation is a background worker that MUTATES cortex state (append-
only; never delete). Each store() call increments memory IDs and adds
rows to the `memories` table. The worker holds a Cortex instance and
calls cortex.store() once per extracted pattern. Source episodics are
never deleted. A processed_ids checkpoint prevents re-extraction of the
same batch across runs.

FACT_CLOUD RELATIONSHIP
───────────────────────
Consolidation does NOT directly manipulate FACT_CLOUD nodes (those are
Ollama-extracted facts from reading, prefixed `FACT_CLOUD_*`). However:
  - Consolidation can extract FACTUAL patterns from episodics that
    discuss FACT_CLOUD nodes. Example: episodic "Igor queried
    FACT_CLOUD_ABC and FACT_CLOUD_XYZ together" → consolidated FACTUAL
    "Concept P and Q are semantically adjacent."
  - Separate module replay.py (D228, D353) handles the FACT_CLOUD →
    topology pass, strengthening co-occurrence edges between FACT_CLOUD
    nodes that appear together in reading sessions. Consolidation +
    replay form a tandem: consolidation works on episodic experience,
    replay on reading deposits.

HEBBIAN CO-OCCURRENCE EDGE STRENGTHENING
────────────────────────────────────────
NOT performed by consolidation.py. Instead, downstream:
  D154  trail-training-hebbian — via tails table (tracing co-activated
        node sequences)
  D233  spreading-activation   — seeds TWM top-7, traverses word_graph +
        memory links
  D358  enable-trail-training  — every search trace creates/strengthens
        co_activation edges via _emit_search_trace()
  D353  sleep-consolidation    — replay.py strengthens edges by delta
        for FACT_CLOUD pairs co-deposited within 120s

Consolidation's role is PRE-Hebbian: it clusters memories and extracts
their meaning. The Hebbian hardening comes downstream via trail training
and replay.py.

SCHEDULING & TRIGGERING
───────────────────────
Trigger     called from main.py _run_consolidation_background() after
             session idle (background drain cycle).
Gate        IGOR_CONSOLIDATION_ENABLED (default true)
Schedule    no more than once per IGOR_CONSOLIDATION_INTERVAL_SECS
             (default 1800 s)
Batch       IGOR_CONSOLIDATION_BATCH (default 200 episodics per run)
Checkpoint  persists last_run_ts + processed_ids (last 1000) to
             {IGOR_INSTANCE_DIR}/consolidation_checkpoint.json,
             preventing re-extraction across restarts.

SLEEP PHASES
────────────
Consolidation itself has no explicit sleep-phase branching (no slow-wave
vs REM). However, it runs DURING quiet periods (idle gate in main.py). A
separate deep-consolidation pass runs in narrative_engine.py
(_deep_consolidation_pass, D353) during longer idle windows — Hebbian
wandering over search traces.

INVESTMENT DECAY (co-located)
─────────────────────────────
At the end of each consolidation pass, cortex.decay_investment_weights()
is called. Some memories end their NRE contracts and are downweighted.
The consolidation checkpoint records which NREs ended for logging.

ENGRAM PORTION (graph-resident)
───────────────────────────────
  PROC_CONSOLIDATION       — habit that runs consolidation on schedule
                              (planned)
  PROC_CONSOLIDATION_STATS — tracks success rate, clusters created,
                              extractions made
  Extracted pattern narratives — themselves become INTERPRETIVE /
                                 FACTUAL engrams, auto-indexed by
                                 word_graph traversal in search

KEY DECISIONS SHAPING THIS SUBSYSTEM
────────────────────────────────────
  D091  memory-store-epic        — unified memory access; consolidation
                                    is part of the architecture
  D154  trail-training-hebbian   — downstream Hebbian edge learning
  D228  consolidation-replay     — FACT_CLOUD topology (see replay.py)
  D233  spreading-activation     — consumes consolidation outputs
  D239  dynamic-ollama-routing   — route() called at call time
  D277  biological-patterns-gap  — lists missing patterns (predictive
                                    coding, lateral inhibition, dopamine
                                    weighting, chunking, refractory,
                                    homeostatic setpoints)
  D353  sleep-consolidation      — idle-triggered quiet-period pass
  D358  enable-trail-training    — Hebbian edge learning co-runs with
                                    consolidation
  D360  reading-model-benchmark  — qwen2.5:7b chosen for JSON extraction

Entry points
────────────
  run_consolidation(cortex)  — execute one pass; returns summary dict
  should_run()               — schedule check
  _load_checkpoint() / _save_checkpoint()
  _cluster_episodics()       — keyword-overlap clustering
  _call_local_llm()          — invoke qwen via Ollama
  _keyword_overlap()         — Jaccard similarity metric

Configuration (env vars)
────────────────────────
  IGOR_CONSOLIDATION_ENABLED        default true
  IGOR_CONSOLIDATION_INTERVAL_SECS  default 1800
  IGOR_CONSOLIDATION_BATCH          default 200
  IGOR_CONSOLIDATION_MIN_IMPORTANCE default 0.4
  IGOR_CONSOLIDATION_MODEL          default qwen2.5:7b
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
_INTERVAL_SECS = lambda: int(os.getenv("IGOR_CONSOLIDATION_INTERVAL_SECS", "1800"))
_BATCH_SIZE = lambda: int(os.getenv("IGOR_CONSOLIDATION_BATCH", "200"))
_MIN_IMPORTANCE = float(os.getenv("IGOR_CONSOLIDATION_MIN_IMPORTANCE", "0.4"))
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
    """Call Ollama chat API to extract a consolidation candidate.

    Uses qwen2.5:7b via cluster router — proven to produce valid JSON
    (D360 benchmark: 16 nodes, 0.78 confidence). DeepSeek 7B distill can't
    do structured extraction (0 nodes in benchmark).
    """
    try:
        import ollama as _ollama

        from .inference_ollama import route as _route

        _host, _model = _route("extraction")
        if not _host:
            from .inference_ollama import OLLAMA_HOST

            _host = OLLAMA_HOST
        # Override model: qwen2.5:7b is the only local model that produces
        # valid JSON extraction (D360 benchmark). 1b and DeepSeek 7B fail.
        _model = os.getenv("IGOR_CONSOLIDATION_MODEL", "qwen2.5:7b")
        _client = _ollama.Client(host=_host)
        # Cap prompt to prevent OOM cascades on CPU-only inference (T-ollama-input-cap).
        _max_chars = int(os.getenv("IGOR_OLLAMA_MAX_USER_CHARS", "8000"))
        if len(prompt) > _max_chars:
            logging.getLogger(__name__).warning(
                "consolidation: truncating prompt %d→%d chars (T-ollama-input-cap)",
                len(prompt),
                _max_chars,
            )
            prompt = prompt[:_max_chars]
        # Timeout to prevent the inference from blocking indefinitely on CPU-only machines.
        import threading as _threading
        import queue as _queue
        _timeout_secs = int(os.getenv("IGOR_CONSOLIDATION_TIMEOUT_SECS", "90"))
        _q: _queue.Queue = _queue.Queue()
        def _do_chat():
            try:
                _q.put((_client.chat(
                    model=_model,
                    messages=[{"role": "user", "content": prompt}],
                    options={"temperature": 0.1, "num_predict": 300},
                ), None))
            except Exception as _e:
                _q.put((None, _e))
        _thread = _threading.Thread(target=_do_chat, daemon=True)
        _thread.start()
        try:
            _resp, _err = _q.get(timeout=_timeout_secs)
        except _queue.Empty:
            raise RuntimeError(f"consolidation LLM timed out after {_timeout_secs}s")
        if _err:
            raise _err
        response = _resp
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
    except Exception as e:
        logging.getLogger(__name__).warning("consolidation LLM call failed: %s", e)
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
