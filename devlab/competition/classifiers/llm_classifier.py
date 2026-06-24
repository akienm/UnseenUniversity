"""
llm_classifier.py — Prompt-first LLM memory type classifier with hash cache.

Personality B in the inference competition (T-classifier-prompt-first).

Algorithm:
  1. SHA256(text) → lookup in competition.classifications_cache.
  2. Cache hit → return stored memory_type with cloud_calls_count=0.
  3. Cache miss → call Ollama with few-shot prompt → parse memory_type.
  4. Store result in cache. Return (memory_type, 1).

Cache is mandatory — without it the classifier burns cloud calls on every
eval row on every run. Cache hits are free (cloud_calls_count=0).

Usage:
    from devlab.competition.classifiers.llm_classifier import classify

    mtype, cloud_calls = classify("Use list comprehensions for fast iteration.")
    # → ("PROCEDURAL", 1) first call, ("PROCEDURAL", 0) on repeat
"""
from __future__ import annotations
from unseen_university.identity import home_db_url

import hashlib
import json
import os
from typing import Optional

import ollama as _ollama
import psycopg2

FALLBACK_TYPE = "FACTUAL"
_MODEL = os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b")
_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# Valid memory types — classifier must return one of these
_VALID_TYPES = {
    "FACTUAL",
    "PROCEDURAL",
    "EPISODIC",
    "INTERPRETIVE",
    "EXPERIENTIAL",
    "CONCEPTUAL",
}

_FEW_SHOT_EXAMPLES = """
Examples:
text: "Python list comprehensions run faster than equivalent for loops in CPython."
type: FACTUAL

text: "To install a package: run `pip install <package>` in your terminal."
type: PROCEDURAL

text: "After the refactor, the test suite finally passed with zero failures."
type: EPISODIC

text: "Object-oriented design often trades simplicity for extensibility — worth it only when extension is actually needed."
type: INTERPRETIVE

text: "When debugging async race conditions, logging timestamps on both sides of every await is the single most useful trick."
type: EXPERIENTIAL

text: "Recursion is a problem-solving strategy where a function calls itself with a smaller subproblem until a base case is reached."
type: CONCEPTUAL
""".strip()

_PROMPT_TEMPLATE = """\
Classify the following text into exactly one memory type.

Memory types:
- FACTUAL: objective facts, statistics, definitions, reference data
- PROCEDURAL: step-by-step instructions, how-to guides, recipes, commands
- EPISODIC: specific events, past experiences, what happened when
- INTERPRETIVE: opinions, analysis, trade-offs, judgments
- EXPERIENTIAL: hard-won lessons, best practices learned through doing
- CONCEPTUAL: explanations of ideas, mental models, theory

{examples}

Respond with JSON only: {{"type": "<TYPE>"}}

text: {text}"""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _conn():
    return psycopg2.connect(home_db_url())


def _ensure_cache_table() -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS competition.classifications_cache (
                        text_hash TEXT PRIMARY KEY,
                        memory_type TEXT NOT NULL,
                        model TEXT NOT NULL DEFAULT '',
                        created_at TIMESTAMPTZ DEFAULT now()
                    )
                    """
                )
    finally:
        conn.close()


def _cache_get(text_hash: str) -> Optional[str]:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT memory_type FROM competition.classifications_cache "
                "WHERE text_hash = %s",
                (text_hash,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _cache_put(text_hash: str, memory_type: str, model: str) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO competition.classifications_cache "
                    "(text_hash, memory_type, model) VALUES (%s, %s, %s) "
                    "ON CONFLICT (text_hash) DO NOTHING",
                    (text_hash, memory_type, model),
                )
    finally:
        conn.close()


def _call_llm(text: str) -> Optional[str]:
    """Call Ollama; return parsed memory_type or None on failure."""
    prompt = _PROMPT_TEMPLATE.format(examples=_FEW_SHOT_EXAMPLES, text=text)
    try:
        client = _ollama.Client(host=_OLLAMA_HOST)
        response = client.chat(
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0},
        )
        raw = response["message"]["content"].strip()
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start < 0 or end <= start:
            return None
        parsed = json.loads(raw[start:end])
        mtype = parsed.get("type", "").strip().upper()
        return mtype if mtype in _VALID_TYPES else None
    except Exception:
        return None


def classify(text: str) -> tuple[str, int]:
    """Classify text as a memory_type using an LLM prompt.

    Returns (memory_type, cloud_calls_count).
    cloud_calls_count is 0 on cache hit, 1 on LLM call.
    Fallback: FACTUAL when LLM unavailable or returns invalid type.
    """
    text_hash = _sha256(text)

    cached = _cache_get(text_hash)
    if cached is not None:
        return cached, 0

    mtype = _call_llm(text)
    if mtype is None:
        return FALLBACK_TYPE, 1

    _cache_put(text_hash, mtype, _MODEL)
    return mtype, 1
