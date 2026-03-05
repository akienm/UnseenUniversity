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

import http.client
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import List, Optional
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ...memory.models import Memory
from ..system_prompt import build_system_prompt
from .base import LocalReasoner

# ── Per-host HTTP connection pool (keep-alive, one connection per host) ────────
_conn_lock = threading.Lock()
_conn_pool: dict[str, http.client.HTTPConnection] = {}


def _post_json(host: str, path: str, payload: dict, timeout: int) -> dict:
    """POST JSON to host/path, reusing keep-alive connection where possible."""
    parsed = urlparse(host)
    netloc  = parsed.netloc  # e.g. "localhost:5001"
    use_ssl = parsed.scheme == "https"

    with _conn_lock:
        conn = _conn_pool.get(netloc)
        if conn is None:
            conn = (http.client.HTTPSConnection if use_ssl else http.client.HTTPConnection)(
                netloc, timeout=timeout
            )
            _conn_pool[netloc] = conn

    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json", "Connection": "keep-alive"}

    try:
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        data = json.loads(resp.read().decode())
        return data
    except Exception:
        # Connection stale — drop and retry once with a fresh connection
        with _conn_lock:
            _conn_pool.pop(netloc, None)
            conn = (http.client.HTTPSConnection if use_ssl else http.client.HTTPConnection)(
                netloc, timeout=timeout
            )
            _conn_pool[netloc] = conn
        conn.request("POST", path, body=body, headers=headers)
        resp = conn.getresponse()
        return json.loads(resp.read().decode())

PREPARSE_TIMEOUT = 8   # seconds — fast extraction only, not reasoning

_PREPARSE_PROMPT = """\
Parse this input. Output ONLY the block below with fields filled in — no other text.

[PARSED_INPUT]
intent: <greeting|question|task|memory_op|command|general>
tone: <friendly|neutral|urgent|frustrated|curious>
complexity: <low|medium|high>
entities: <comma-separated names/things, or none>
requires_tools: <true|false>
memory_hints: <comma-separated keywords relevant to memory search, or none>
habit_id: <matching habit ID from list below, or null>
should_escalate: <true|false>

Input: "{text}"
Habits: {habits}
"""


def _rule_based_csb(user_input: str, habits: List[Memory]) -> str:
    """Fallback: produce PARSED_INPUT CSB block using pure Python rules."""
    text = user_input.lower()
    words = text.split()

    # Intent
    if any(w in text for w in ["hello", "hi ", "hey ", "good morning", "good evening"]):
        intent = "greeting"
    elif any(w in text for w in ["remember", "note that", "save", "learn that"]):
        intent = "memory_op"
    elif text.startswith("/"):
        intent = "command"
    elif "?" in text:
        intent = "question"
    elif any(w in text for w in ["do ", "run ", "execute", "search", "find", "browse"]):
        intent = "task"
    else:
        intent = "general"

    # Tone
    if any(w in text for w in ["urgent", "asap", "immediately", "!"]):
        tone = "urgent"
    elif any(w in text for w in ["frustrated", "annoyed", "broken", "wrong"]):
        tone = "frustrated"
    elif any(w in text for w in ["hello", "hi", "hey", "thanks", "please"]):
        tone = "friendly"
    elif "?" in text:
        tone = "curious"
    else:
        tone = "neutral"

    # Complexity signals
    signals = []
    if len(words) > 50:
        signals.append("long_input")
    if text.count("?") > 1:
        signals.append("multiple_questions")
    if any(w in text for w in ["complex", "complicated", "difficult"]):
        signals.append("self_declared_complex")
    if text.count(",") > 3 or text.count(";") > 1:
        signals.append("complex_structure")
    if any(w in text for w in ["code", "function", "class", "algorithm"]):
        signals.append("code_related")
    complexity = "high" if len(signals) >= 3 else "medium" if len(signals) >= 1 else "low"
    should_escalate = len(signals) >= 2

    # Habit match
    habit_id = "null"
    for h in habits:
        trigger = h.metadata.get("trigger", "").lower()
        if trigger and trigger in text:
            habit_id = h.id
            break

    requires_tools = intent in ("task", "command") or any(
        w in text for w in ["search", "run", "execute", "file", "browse", "read"]
    )

    stop_words = {"the","a","an","is","are","was","i","you","it","in","on","at","to","for","of","and","or"}
    keywords = [w for w in re.findall(r'\b[a-zA-Z]{4,}\b', text) if w not in stop_words]
    memory_hints = ", ".join(keywords[:4]) if keywords else "none"

    return (
        f"[PARSED_INPUT]\n"
        f"intent: {intent}\n"
        f"tone: {tone}\n"
        f"complexity: {complexity}\n"
        f"entities: none\n"
        f"requires_tools: {str(requires_tools).lower()}\n"
        f"memory_hints: {memory_hints}\n"
        f"habit_id: {habit_id}\n"
        f"should_escalate: {str(should_escalate).lower()}\n"
    )


def preparse(user_input: str, habits: List[Memory], host: str = "") -> str:
    """
    Pre-parse user input via KoboldCpp LLM → PARSED_INPUT CSB block.
    Falls back to rule-based CSB on timeout or error.
    Returns a CSB string (always — never raises).
    """
    habit_desc = ", ".join(
        f"{h.id}:{h.metadata.get('trigger','')}" for h in habits if h.metadata.get("trigger")
    ) or "none"

    _host = host or DEFAULT_HOST
    prompt = _PREPARSE_PROMPT.format(
        text=user_input[:300],
        habits=habit_desc[:200],
    )

    payload = {
        "model": os.getenv("KOBOLDCPP_MODEL", "hugging-quants/Llama-3.2-1B-Instruct-Q4_K_M-GGUF"),
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 120,
        "temperature": 0.1,
        "cache_prompt": True,
    }
    try:
        data = _post_json(_host, CHAT_ENDPOINT, payload, PREPARSE_TIMEOUT)
        text = data["choices"][0]["message"]["content"]
        if "[PARSED_INPUT]" in text:
            return text.strip()
    except Exception:
        pass

    return _rule_based_csb(user_input, habits)


def parse_preparse_csb(csb: str, habits: List[Memory]) -> dict:
    """
    Extract routing dict from a PARSED_INPUT CSB block.
    Returns keys compatible with existing main.py routing logic.
    Always returns a valid dict — never raises.
    """
    fields: dict = {}
    for line in csb.splitlines():
        if ":" in line and not line.startswith("["):
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip()

    intent       = fields.get("intent", "general")
    complexity   = fields.get("complexity", "low")
    should_esc   = fields.get("should_escalate", "false").lower() == "true"
    requires_tools = fields.get("requires_tools", "false").lower() == "true"
    habit_id_str = fields.get("habit_id", "null").strip()

    # Derive numeric complexity score for existing callers
    score = {"low": 0.1, "medium": 0.4, "high": 0.8}.get(complexity, 0.1)
    is_multi_unit = score > 0.6 or requires_tools
    tier_minimum  = "tier.4" if score > 0.4 else "tier.3"

    # Resolve habit
    habit_match = None
    if habit_id_str and habit_id_str != "null" and habits:
        habit_match = next((h for h in habits if str(h.id) == habit_id_str), None)

    confidence = 0.9 if habit_match else (0.5 if should_esc else 0.8)

    return {
        "intent":           intent,
        "should_escalate":  should_esc,
        "habit_match":      habit_match,
        "confidence":       confidence,
        "complexity": {
            "score":         score,
            "signals_fired": [complexity] if complexity != "low" else [],
            "tier_minimum":  tier_minimum,
            "is_multi_unit": is_multi_unit,
        },
        "_csb": csb,
    }


def score_memories(user_input: str, candidates: List[Memory]) -> List[Memory]:
    """Score candidate memories for relevance to input. Returns sorted list."""
    if not candidates:
        return []

    keywords = set(re.findall(r'\b\w+\b', user_input.lower()))

    def score(mem: Memory) -> float:
        mem_keywords = set(re.findall(r'\b\w+\b', mem.narrative.lower()))
        overlap = len(keywords & mem_keywords)
        return overlap / max(len(keywords), len(mem_keywords))

    scored = [(score(m), m) for m in candidates]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored if _ > 0.1]

# ── Config ─────────────────────────────────────────────────────────────────────

DEFAULT_HOST         = os.getenv("KOBOLDCPP_HOST", "http://localhost:5001")
DEFAULT_CONTEXT_SIZE = int(os.getenv("KOBOLDCPP_CONTEXT_SIZE", "8192"))
HEALTH_ENDPOINT      = "/api/extra/version"     # KoboldCpp status endpoint
CHAT_ENDPOINT        = "/v1/chat/completions"   # OpenAI-compat endpoint
TIMEOUT              = 30                       # seconds per request

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
        timeout: int = TIMEOUT,
    ):
        self.host         = host.rstrip("/")
        self.context_size = context_size
        self.timeout      = timeout
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
        preparse_csb: str = "",
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
            # KoboldCpp extensions
            "context_size": ctx_size,  # per-request KV cache allocation
            "cache_prompt": True,      # reuse KV state for common system prompt prefix
        }

        t0 = time.perf_counter()
        try:
            data       = _post_json(self.host, CHAT_ENDPOINT, payload, self.timeout)
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
