"""
KoboldCpp local reasoner — Change 1 / D025.

KoboldCpp is built on llama.cpp and exposes an OpenAI-compatible endpoint
(/v1/chat/completions) as well as its own native API.  Key advantage over Ollama:
per-request context_size control, which lets Igor tune context window to task size
without global model reload.

Replaces Ollama as the preferred local inference backend.  Ollama remains as
fallback during transition (see LocalReasonerPool in local_pool.py).

Config:
  KOBOLDCPP_HOST         — default "http://localhost:5001"
  KOBOLDCPP_CONTEXT_SIZE — default 8192 (conservative; override per request)
  KOBOLDCPP_MODEL        — informational label; default "hugging-quants/Llama-3.2-1B-Instruct-Q4_K_M-GGUF"

Logging: every call → koboldcpp_calls.log (same format as ollama_calls.log)
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import URLError

import re
from typing import List
from ...memory.models import Memory
from .base import LocalReasoner
from ..system_prompt import build_system_prompt

def preparse(user_input: str, habits: List[Memory]) -> dict:
    """Pre-parse user input to detect intent, habits, etc."""
    # Simple keyword-based intent detection as fallback
    intent = "general"
    text = user_input.lower()
    
    if any(w in text for w in ["hello", "hi", "hey", "morning", "evening"]):
        intent = "greeting"
    elif any(w in text for w in ["how do you", "what are you", "who are you"]):
        intent = "meta_question"
    elif any(w in text for w in ["why", "explain", "how come"]):
        intent = "explanation"
    elif any(w in text for w in ["remember", "note", "save"]):
        intent = "memory_store"
        
    # Check for habit matches
    habit_match = None
    for habit in habits:
        trigger = habit.metadata.get("trigger", "").lower()
        if trigger and trigger in text:
            habit_match = habit
            break
            
    return {
        "intent": intent,
        "should_escalate": "complex" in text or len(user_input.split()) > 50,
        "habit_match": habit_match,
        "confidence": 0.8 if habit_match else 0.6
    }

def score_memories(user_input: str, candidates: List[Memory]) -> List[Memory]:
    """Score candidate memories for relevance to input. Returns sorted list."""
    if not candidates:
        return []
        
    # Simple keyword matching for now
    keywords = set(re.findall(r'\b\w+\b', user_input.lower()))
    
    def score(mem: Memory) -> float:
        mem_keywords = set(re.findall(r'\b\w+\b', mem.narrative.lower()))
        overlap = len(keywords & mem_keywords)
        return overlap / max(len(keywords), len(mem_keywords))
        
    scored = [(score(m), m) for m in candidates]
    scored.sort(reverse=True)
    return [m for _, m in scored if _ > 0.1]  # Only return reasonable matches

def compute_complexity(user_input: str) -> dict:
    """Estimate task complexity to guide tier selection."""
    text = user_input.lower()
    words = text.split()
    
    signals = []
    if len(words) > 50:
        signals.append("long_input")
    if "?" in text and text.count("?") > 1:
        signals.append("multiple_questions")
    if any(w in text for w in ["complex", "complicated", "difficult"]):
        signals.append("self_declared_complex")
    if text.count(",") > 3 or text.count(";") > 1:
        signals.append("complex_structure")
    if any(w in text for w in ["code", "function", "class", "algorithm"]):
        signals.append("code_related")
        
    score = len(signals) * 0.2  # 0.0 to 1.0
    
    return {
        "score": min(1.0, score),
        "signals_fired": signals,
        "tier_minimum": "tier.4" if score > 0.4 else "tier.3",
        "is_multi_unit": score > 0.6
    }

# ── Config ─────────────────────────────────────────────────────────────────────

DEFAULT_HOST         = os.getenv("KOBOLDCPP_HOST", "http://localhost:5001")
DEFAULT_CONTEXT_SIZE = int(os.getenv("KOBOLDCPP_CONTEXT_SIZE", "8192"))
HEALTH_ENDPOINT      = "/api/v1/info"           # KoboldCpp status endpoint
CHAT_ENDPOINT        = "/v1/chat/completions"   # OpenAI-compat endpoint
TIMEOUT              = 120                      # seconds per request

# ── Call logger ────────────────────────────────────────────────────────────────

_LOG_PATH = Path(__file__).parent.parent.parent.parent / "koboldcpp_calls.log"
_LOG_PATH = _LOG_PATH.resolve()

_kcc_log = logging.getLogger("igor.koboldcpp_calls")
if not _kcc_log.handlers:
    _kcc_log.setLevel(logging.DEBUG)
    _fh = logging.FileHandler(str(_LOG_PATH), encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _kcc_log.addHandler(_fh)
    _kcc_log.propagate = False


def _log_call(
    fn_name: str,
    host: str,
    elapsed: float,
    tokens_in: int = 0,
    tokens_out: int = 0,
    error: Optional[str] = None,
):
    tok_per_sec = round(tokens_out / elapsed, 1) if elapsed > 0 and tokens_out else 0.0
    if error:
        _kcc_log.info(
            f"fn={fn_name} host={host} elapsed_ms={elapsed*1000:.1f} ok=False error={error!r}"
        )
    else:
        _kcc_log.info(
            f"fn={fn_name} host={host} elapsed_ms={elapsed*1000:.1f} "
            f"tokens_in={tokens_in} tokens_out={tokens_out} "
            f"tok_per_sec={tok_per_sec} ok=True"
        )


# ── Health check (for boot_check.py) ──────────────────────────────────────────

def is_healthy(host: str = DEFAULT_HOST, timeout: int = 5) -> bool:
    """
    Check if KoboldCpp is running at `host` by probing HEALTH_ENDPOINT.
    Returns True if the API responds with a 200 and valid JSON.
    """
    url = host.rstrip("/") + HEALTH_ENDPOINT
    try:
        with urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        return bool(data)  # Any truthy JSON response means it's up
    except (URLError, OSError, json.JSONDecodeError, ValueError):
        return False


# ── Reasoner ───────────────────────────────────────────────────────────────────

class KoboldCppReasoner(LocalReasoner):
    """
    Local reasoning via KoboldCpp OpenAI-compatible API.

    Advantages over Ollama:
      - Per-request context_size — tune window to task without global reload
      - llama.cpp foundation — well-optimised CPU/GPU inference
      - Better visibility into generation parameters

    supports_context_param = True (unique to KoboldCpp among local reasoners).
    """

    supports_context_param: bool = True

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        context_size: int = DEFAULT_CONTEXT_SIZE,
        model_label: str = "",
    ):
        self.host         = host.rstrip("/")
        self.context_size = context_size
        # model_label is informational — the actual model is managed inside KoboldCpp
        self._model_label = model_label or os.getenv(
            "KOBOLDCPP_MODEL",
            "hugging-quants/Llama-3.2-1B-Instruct-Q4_K_M-GGUF",
        )

    def name(self) -> str:
        return f"KoboldCpp/{self._model_label}@{self.host}"

    def reason(
        self,
        user_input: str,
        relevant_memories: list[Memory],
        core_patterns: list[Memory],
        instance_id: str,
        cortex=None,
        context_size: Optional[int] = None,
    ) -> tuple[str, float]:
        """
        Generate a response via KoboldCpp's /v1/chat/completions endpoint.
        context_size can be overridden per call; defaults to self.context_size.
        Returns (response_text, 0.0) — local inference has no API cost.
        """
        system = build_system_prompt(cortex, instance_id)

        memory_context = ""
        if relevant_memories:
            memory_context = "\n\nRelevant memories:\n" + "\n".join(
                f"- {m.narrative}" for m in relevant_memories[:5]
            )

        ctx_size = context_size if context_size is not None else self.context_size

        payload = {
            "model": self._model_label,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user",   "content": user_input + memory_context},
            ],
            "max_tokens": min(512, ctx_size // 4),
            "temperature": 0.3,
            # KoboldCpp extension: pass context size so it can allocate the KV cache
            "context_size": ctx_size,
        }

        url  = self.host + CHAT_ENDPOINT
        body = json.dumps(payload).encode("utf-8")
        req  = Request(
            url, data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        t0 = time.perf_counter()
        try:
            with urlopen(req, timeout=TIMEOUT) as resp:
                data     = json.loads(resp.read().decode())
            elapsed    = time.perf_counter() - t0
            text       = data["choices"][0]["message"]["content"]
            usage      = data.get("usage", {})
            tokens_in  = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)
            _log_call("KoboldCppReasoner.reason", self.host, elapsed,
                      tokens_in, tokens_out)
            return text, 0.0
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            _log_call("KoboldCppReasoner.reason", self.host, elapsed, error=str(exc))
            raise
