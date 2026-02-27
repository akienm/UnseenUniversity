"""
Ollama local reasoner.
Runs on-device. No API cost. Used for fast pre-parsing and habit matching,
not for heavy reasoning (that stays with Anthropic).

Call logging: every Ollama call writes a structured entry to ollama_calls.log
with timing, token counts, and tokens/sec so we can tune model selection.
"""

import json
import logging
import os
import time
import ollama as _ollama
from ...memory.models import Memory
from .base import BaseReasoner

DEFAULT_MODEL = "gemma3:270M"

# ── Ollama call logger ──────────────────────────────────────────────────────
_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "..", "ollama_calls.log")
_LOG_PATH = os.path.normpath(_LOG_PATH)

_ollama_log = logging.getLogger("igor.ollama_calls")
if not _ollama_log.handlers:
    _ollama_log.setLevel(logging.DEBUG)
    _fh = logging.FileHandler(_LOG_PATH, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _ollama_log.addHandler(_fh)
    _ollama_log.propagate = False  # don't bubble up to root logger


def _log_call(fn_name: str, model: str, response, elapsed: float, error: str | None = None):
    """
    Write one structured log line per Ollama call.
    Fields: function | model | elapsed_ms | tokens_in | tokens_out | tok_per_sec | ok | error
    """
    if error:
        _ollama_log.info(
            f"fn={fn_name} model={model} elapsed_ms={elapsed*1000:.1f} "
            f"ok=False error={error!r}"
        )
        return

    tokens_in  = getattr(response, "prompt_eval_count", None) or response.get("prompt_eval_count", 0)
    tokens_out = getattr(response, "eval_count", None) or response.get("eval_count", 0)
    tok_per_sec = round(tokens_out / elapsed, 1) if elapsed > 0 and tokens_out else 0.0

    _ollama_log.info(
        f"fn={fn_name} model={model} elapsed_ms={elapsed*1000:.1f} "
        f"tokens_in={tokens_in} tokens_out={tokens_out} "
        f"tok_per_sec={tok_per_sec} ok=True"
    )


# ── Reasoner class ──────────────────────────────────────────────────────────

class OllamaReasoner(BaseReasoner):
    """Full reasoning via local or remote Ollama model. Slow but free."""

    def __init__(self, model: str = DEFAULT_MODEL, host: str | None = None):
        self.model = model
        self.host = host  # None = localhost; e.g. "http://10.0.0.99:11434" for remote
        self._client = _ollama.Client(host=host) if host else _ollama

    def name(self) -> str:
        label = self.host or "local"
        return f"Ollama/{self.model}@{label}"

    def reason(
        self,
        user_input: str,
        relevant_memories: list[Memory],
        core_patterns: list[Memory],
        instance_id: str,
    ) -> tuple[str, float]:
        memory_context = ""
        if relevant_memories:
            memory_context = "\n\nRelevant memories:\n" + "\n".join(
                f"- {m.narrative}" for m in relevant_memories[:5]
            )

        t0 = time.perf_counter()
        try:
            response = self._client.chat(
                model=self.model,
                messages=[{"role": "user", "content": user_input + memory_context}],
            )
            elapsed = time.perf_counter() - t0
            _log_call("OllamaReasoner.reason", self.model, response, elapsed)
            return response["message"]["content"], 0.0  # Local = no cost
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            _log_call("OllamaReasoner.reason", self.model, None, elapsed, error=str(exc))
            raise


# ── preparse ────────────────────────────────────────────────────────────────

def preparse(user_input: str, habits: list[Memory], model: str = DEFAULT_MODEL) -> dict:
    """
    Use local 1B model to cheaply preprocess input before touching the API.

    Returns a dict with:
      - intent: classified intent string
      - keywords: list of key terms
      - habit_match: Memory or None if a habit likely applies
      - confidence: 0.0-1.0 how confident we are a habit covers this
      - should_escalate: bool - True means send to Anthropic API
    """
    habit_desc = ""
    if habits:
        habit_desc = "\n\nAvailable habits:\n" + "\n".join(
            f"- ID={h.id}: trigger='{h.metadata.get('trigger', '')}' desc='{h.narrative[:60]}'"
            for h in habits
        )

    prompt = f"""Classify this user input. Reply with ONLY a JSON object, no other text.

User input: "{user_input}"{habit_desc}

JSON fields:
- intent: one word from this list only: greeting, meta_question, factual_question, action_request, memory_instruction, general
- keywords: array of 2-4 important words from the input
- habit_id: the habit ID string if a habit matches, or null
- confidence: number from 0.0 to 1.0 for how well a habit matches
- should_escalate: true if needs deep reasoning, false if simple

Example output:
{{"intent": "factual_question", "keywords": ["capital", "france"], "habit_id": null, "confidence": 0.0, "should_escalate": true}}"""

    t0 = time.perf_counter()
    try:
        response = _ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1},
        )
        elapsed = time.perf_counter() - t0
        _log_call("preparse", model, response, elapsed)

        text = response["message"]["content"].strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end])
        else:
            raise ValueError("No JSON found in response")

        habit_match = None
        if parsed.get("habit_id") and habits:
            habit_match = next((h for h in habits if h.id == parsed["habit_id"]), None)

        return {
            "intent": parsed.get("intent", "general"),
            "keywords": parsed.get("keywords", []),
            "habit_match": habit_match,
            "confidence": float(parsed.get("confidence", 0.0)),
            "should_escalate": bool(parsed.get("should_escalate", True)),
        }

    except Exception as exc:
        elapsed = time.perf_counter() - t0
        _log_call("preparse", model, None, elapsed, error=str(exc))
        # If local model fails, escalate to API - never block on local failure
        return {
            "intent": "general",
            "keywords": [],
            "habit_match": None,
            "confidence": 0.0,
            "should_escalate": True,
        }


# ── score_memories ──────────────────────────────────────────────────────────

def score_memories(
    query: str,
    memories: list[Memory],
    model: str = DEFAULT_MODEL,
    top_n: int = 5,
) -> list[Memory]:
    """
    Use local model to score memory relevance rather than naive text search.
    Returns top_n most relevant memories.
    """
    if not memories:
        return []

    mem_list = "\n".join(
        f"{i}: [{m.memory_type.value}] {m.narrative[:80]}"
        for i, m in enumerate(memories[:20])
    )

    prompt = f"""Given this query: "{query}"

Rate each memory's relevance (0-10). Reply with ONLY a JSON array of [index, score] pairs, most relevant first. Example: [[2,9],[0,7],[1,3]]

Memories:
{mem_list}"""

    t0 = time.perf_counter()
    try:
        response = _ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.1},
        )
        elapsed = time.perf_counter() - t0
        _log_call("score_memories", model, response, elapsed)

        text = response["message"]["content"].strip()
        start = text.find("[")
        end = text.rfind("]") + 1
        if start < 0:
            return memories[:top_n]

        scores = json.loads(text[start:end])
        ranked = sorted(scores, key=lambda x: x[1], reverse=True)
        result = []
        for idx, score in ranked[:top_n]:
            if 0 <= idx < len(memories) and score > 0:
                result.append(memories[idx])
        return result if result else memories[:top_n]

    except Exception as exc:
        elapsed = time.perf_counter() - t0
        _log_call("score_memories", model, None, elapsed, error=str(exc))
        return memories[:top_n]


# ── summarize_session ────────────────────────────────────────────────────────

def summarize_session(
    ring_entries: list[dict],
    instance_id: str,
    model: str = DEFAULT_MODEL,
) -> str:
    """
    Compress ring memory entries into a CSB (Compressed Semantic Block).
    Uses local Ollama — free and fast. Returns a dense summary string suitable
    for storing as an INTERPRETIVE memory (cold-readable by future Igor).
    Falls back to a simple join if Ollama fails.
    """
    if not ring_entries:
        return f"SESSION_SUMMARY|{instance_id}|empty_session"

    # Format entries for the prompt — skip internal noise
    relevant = [
        e for e in ring_entries
        if e.get("category") not in ("tool_trace", "interruptor")
    ][-30:]

    entries_text = "\n".join(
        f"[{e['timestamp'][11:16]}][{e['category']}] {e['content'][:200]}"
        for e in relevant
    )

    prompt = f"""You are a memory compression system for an AI agent called Igor (instance: {instance_id}).

Compress these session ring-memory entries into a dense CSB (Compressed Semantic Block).

Ring memory (oldest first):
{entries_text}

Write a 100-150 word dense summary covering:
- Main topics/tasks worked on
- Key decisions or changes made
- Tools used and outcomes
- Current state and any pending work
- Emotional tone/valence of the session

Format: one paragraph, dense, information-rich, written for cold reading by a future Igor instance.
No preamble. Start directly with the content."""

    t0 = time.perf_counter()
    try:
        response = _ollama.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.2},
        )
        elapsed = time.perf_counter() - t0
        _log_call("summarize_session", model, response, elapsed)
        summary = response["message"]["content"].strip()
        return f"SESSION_SUMMARY|{instance_id}|{summary}"
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        _log_call("summarize_session", model, None, elapsed, error=str(exc))
        # Fallback: join last few entries manually
        lines = [e["content"][:120] for e in relevant[-5:]]
        return f"SESSION_SUMMARY|{instance_id}|fallback: " + " | ".join(lines)
