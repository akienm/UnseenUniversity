"""
node_extractor.py — LLM-based knowledge extraction for ReaderDevice nodes mode.

Generalizes book_learner.py's extraction logic into a source-agnostic layer.
No deposit happens here — returns node dicts the caller stores however it likes.

Node shape (each element of the returned list):
    {
        "narrative": str,          # 1-2 sentences, present tense, self-contained
        "memory_type": str,        # "FACTUAL" | "INTERPRETIVE" | "PROCEDURAL"
        "metadata": {
            "source_uri": str,
            "chunk_position": int,
            "source_title": str,   # if known
            "source_author": str,  # if known
            "model_used": str,
            "extracted_at": str,   # ISO 8601
            "confidence": float,
            "node_type": str,      # raw LLM type before mapping
        }
    }

Routing: dispatches with task_class='worker', domain='' — the cost-optimizing
router picks the cheapest capable source (no per-device model pin).
The injected inference device controls the backend (INFERENCE_MODE=ollama|openrouter).

D-reader-device-unified-uri-2026-05-28
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_CONFIDENCE_THRESHOLD = 0.60

_EXTRACT_PROMPT = """\
You are a knowledge extraction system. Read the passage below and extract 1-5 \
memory nodes representing generalizable knowledge worth retaining.

NODE TYPES:
  factual      — a concept, definition, or empirical fact
  interpretive — a connection: "when X, it implies Y"
  procedural   — an action pattern with a clear trigger

RESPONSE FORMAT — output ONLY valid JSON, no markdown, no extra text:
{
  "nodes": [
    {
      "type": "factual|interpretive|procedural",
      "narrative": "1-2 sentences: the generalizable knowledge, present tense, self-contained",
      "confidence": 0.0-1.0
    }
  ]
}

Rules:
- 1-5 nodes per chunk. Every passage has at least one insight worth capturing.
- Confidence < 0.60 will be filtered. Set honestly.
- Narratives must be self-contained — no "this passage" or "the author says".
- Present tense. Generalizable. Not domain-locked to this specific text.
"""

_TYPE_MAP: dict[str, str] = {
    "factual": "FACTUAL",
    "interpretive": "INTERPRETIVE",
    "procedural": "PROCEDURAL",
    "mechanism": "INTERPRETIVE",
    "lever": "PROCEDURAL",
    "situated": "INTERPRETIVE",
    "tension": "INTERPRETIVE",
}


def _clean_json(raw: str) -> str:
    """Strip markdown fences and leading/trailing whitespace."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return raw.strip()


def _extract_chunk(
    chunk: str,
    chunk_pos: int,
    source_uri: str,
    inference: Any,
    *,
    source_title: str = "",
    source_author: str = "",
) -> list[dict]:
    """Extract knowledge nodes from one text chunk via LLM. Returns empty list on error."""
    from unseen_university.devices.inference.shim import InferenceRequest

    user_content = "PASSAGE"
    if source_title:
        user_content += f" (from: {source_title})"
    user_content += f":\n\n{chunk}"

    req = InferenceRequest(
        messages=[
            {"role": "system", "content": _EXTRACT_PROMPT},
            {"role": "user", "content": user_content},
        ],
        # Route by domain — entity/relation extraction is a moderate generalist task.
        task_class="worker",
        domain="",
        max_tokens=1024,
        temperature=0.2,
    )

    try:
        resp = inference.dispatch(req)
        model_used = getattr(resp, "model", "") or "router-selected"
        raw = resp.text.strip()
        parsed = json.loads(_clean_json(raw))
    except json.JSONDecodeError as exc:
        log.warning("node_extractor: JSON parse error at chunk %d: %s", chunk_pos, exc)
        return []
    except Exception as exc:
        log.warning("node_extractor: inference error at chunk %d: %s", chunk_pos, exc)
        return []

    extracted_at = datetime.now(timezone.utc).isoformat()
    nodes: list[dict] = []

    for raw_node in parsed.get("nodes", []):
        ntype = raw_node.get("type", "factual").strip().lower()
        narrative = raw_node.get("narrative", "").strip()
        confidence = float(raw_node.get("confidence", 0.0))

        if not narrative or confidence < _CONFIDENCE_THRESHOLD:
            continue

        memory_type = _TYPE_MAP.get(ntype, "FACTUAL")
        meta: dict = {
            "source_uri": source_uri,
            "chunk_position": chunk_pos,
            "model_used": model_used,
            "extracted_at": extracted_at,
            "confidence": confidence,
            "node_type": ntype,
        }
        if source_title:
            meta["source_title"] = source_title
        if source_author:
            meta["source_author"] = source_author

        nodes.append(
            {
                "narrative": narrative,
                "memory_type": memory_type,
                "metadata": meta,
            }
        )

    return nodes


def extract_nodes(
    chunks: list[str],
    source_uri: str,
    inference: Any,
    *,
    source_title: str = "",
    source_author: str = "",
) -> list[dict]:
    """Extract knowledge nodes from all chunks. Returns flat list across all chunks."""
    nodes: list[dict] = []
    for pos, chunk in enumerate(chunks):
        chunk_nodes = _extract_chunk(
            chunk,
            pos,
            source_uri,
            inference,
            source_title=source_title,
            source_author=source_author,
        )
        nodes.extend(chunk_nodes)
    return nodes
