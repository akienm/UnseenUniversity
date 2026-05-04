"""
Ollama local reasoner — primary local inference backend.

Tier.2 local reasoning and preparse. Host selection is dynamic via
cluster_router — never frozen at import time. Embeddings always stay on
localhost (embedder.py uses _ollama default directly).

Config:
  OLLAMA_LOCAL_MODEL  — fallback model if router returns none (default: llama3.2:1b)
  OLLAMA_HOST         — fallback host if router returns none (default: http://localhost:11434)

Call logging: every Ollama call writes a structured entry to ollama_calls.log
with timing, token counts, and tokens/sec so we can tune model selection.
"""

import json
import logging
import os
import queue as _queue
import re
import threading as _threading
import time
import urllib.request
from urllib.error import URLError
import ollama as _ollama
from ...igor_base import get_logger
from ...memory.models import Memory
from ...paths import paths
from .base import BaseReasoner, LocalReasoner
from ..system_prompt import build_system_prompt
from ..forensic_logger import log_error
from ...igor_base import IgorBase

OLLAMA_LOCAL_MODEL = os.getenv("OLLAMA_LOCAL_MODEL", "llama3.2:1b")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = OLLAMA_LOCAL_MODEL  # backwards-compat alias


def _get_client_and_model(call_type: str) -> tuple[object, str]:
    """Ask cluster_router for the best host+model. Falls back to localhost."""
    try:
        from ..cluster_router import route as _route

        host, model = _route(call_type)
        if host and model:
            client = _ollama.Client(host=host) if host != OLLAMA_HOST else _ollama
            return client, model
    except Exception as _e:
        get_logger(__name__).warning(
            "[ollama_reasoner] cluster_router failed (%s) — using localhost", _e
        )
    return _ollama, OLLAMA_LOCAL_MODEL


# rule: theigors/rules/local-inference-no-timeouts — local takes whatever
# time it takes; brain-modeled goal makes local-fast NOT a constraint.
# Sanity cap of 1hr to catch a truly hung process (e.g., model deadlock).
# Preparse falls back to _rule_based_csb on hit, so the cap is the LATEST
# the user can wait before getting a rule-based parse instead of an LLM one.
PREPARSE_TIMEOUT = 3600  # seconds (1hr — sanity cap, not a UX deadline)

# Intent taxonomy must match thalamus.py 13-intent taxonomy exactly (#30, G36)
_PREPARSE_PROMPT = """\
Parse this input. Output ONLY the block below with fields filled in — no other text.

[PARSED_INPUT]
intent: <greeting|meta_question|memory_instruction|code_task|analysis_task|explanation_request|factual_question|action_request|complaint|command|conversation|creative_request|general>
tone: <friendly|neutral|urgent|frustrated|curious>
complexity: <low|medium|high>
entities: <comma-separated names/things, or none>
requires_tools: <true|false>
memory_hints: <comma-separated keywords relevant to memory search, or none>
should_escalate: <true|false>

Input: "{text}"
"""

# ── Ollama call logger ──────────────────────────────────────────────────────
_LOG_PATH = paths().logs / "ollama_calls.log"
paths().logs.mkdir(parents=True, exist_ok=True)

_ollama_log = logging.getLogger("igor.ollama_calls")
if not _ollama_log.handlers:
    _ollama_log.setLevel(logging.DEBUG)
    _fh = logging.FileHandler(str(_LOG_PATH), encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _ollama_log.addHandler(_fh)
    _ollama_log.propagate = False  # don't bubble up to root logger


# ── Health check ────────────────────────────────────────────────────────────


# NOTE: is_healthy is an HTTP availability ping (GET /api/tags), NOT
# inference — so theigors/rules/local-inference-no-timeouts does NOT apply
# here. 5s is appropriate: a healthy Ollama answers /api/tags in <1s.
def is_healthy(host: str | None = None, timeout: int = 5) -> bool:
    """Return True if Ollama is running at host (probes /api/tags).
    If host is None, probes the current best routed host."""
    if host is None:
        try:
            from ..cluster_router import route as _route

            routed_host, _ = _route("tier2")
            host = routed_host or OLLAMA_HOST
        except Exception:
            host = OLLAMA_HOST
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=timeout) as resp:
            return resp.status == 200
    except (URLError, OSError):
        return False


# ── CSB preparse (13-intent) ──────────────────────────────────────────────────


def _rule_based_csb(user_input: str, habits: list) -> str:
    """Pure-Python fallback: produce PARSED_INPUT CSB block without LLM.
    Intent taxonomy matches thalamus.py 13-intent taxonomy (#30, G36).
    """
    text = user_input.lower()
    words = text.split()

    if any(
        w in text
        for w in [
            "hello",
            "hi ",
            "hey ",
            "good morning",
            "good evening",
            "howdy",
            "how are you",
        ]
    ):
        intent = "greeting"
    elif any(w in text for w in ["remember", "note that", "save", "learn that"]):
        intent = "memory_instruction"
    elif text.startswith("/"):
        intent = "command"
    elif any(
        w in text
        for w in [
            "what about igor",
            "tell me about yourself",
            "what are you",
            "who are you",
            "how do you work",
            "what can you do",
            "how do you",
            "what do you do",
        ]
    ):
        intent = "meta_question"
    elif any(
        w in text
        for w in [
            "explain",
            "how does",
            "how do i",
            "describe",
            "walk me through",
            "why did you",
            "why are you",
            "how does that",
            "what does that mean",
        ]
    ):
        intent = "explanation_request"
    elif any(
        w in text
        for w in [
            "write code",
            "fix this",
            "debug",
            "implement",
            "refactor",
            "code",
            "function",
            "class",
            "algorithm",
            "script",
            "program",
        ]
    ):
        intent = "code_task"
    elif any(
        w in text
        for w in [
            "analyze",
            "analyse",
            "compare",
            "review",
            "assess",
            "evaluate",
            "summarize",
            "summarise",
            "audit",
        ]
    ):
        intent = "analysis_task"
    elif any(
        w in text
        for w in [
            "broken",
            "wrong",
            "doesn't work",
            "not working",
            "failed",
            "frustrated",
            "annoyed",
        ]
    ):
        intent = "complaint"
    elif any(
        w in text
        for w in [
            "what do you think",
            "do you agree",
            "do you reckon",
            "i think",
            "i feel",
            "i find that",
            "i believe",
            "what's your opinion",
            "thoughts on",
        ]
    ):
        intent = "conversation"
    elif any(
        w in text
        for w in [
            "read me",
            "read to me",
            "tell me a story",
            "write me a poem",
            "write me a story",
            "let's read",
            "read aloud",
            "narrate",
            "sing me",
            "recite",
            "read through",
            # reading session patterns — collaborative, foreground, interactive
            "start at chapter",
            "start reading",
            "reading each sentence",
            "read each sentence",
            "let it sit",
            "we talk about it",
            "then we talk",
            "then we discuss",
            "your assessment",
            "chapter by chapter",
            "read together",
            "reading together",
            "sentence by sentence",
        ]
    ):
        intent = "creative_request"
    elif "?" in text:
        intent = "factual_question"
    elif any(
        w in text
        for w in [
            "do ",
            "run ",
            "execute",
            "search",
            "find",
            "browse",
            "send",
            "create",
            "delete",
        ]
    ):
        intent = "action_request"
    else:
        intent = "general"

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
    complexity = (
        "high" if len(signals) >= 3 else "medium" if len(signals) >= 1 else "low"
    )
    should_escalate = len(signals) >= 2

    requires_tools = intent in ("action_request", "command", "code_task") or any(
        w in text for w in ["search", "run", "execute", "file", "browse", "read"]
    )

    stop_words = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "i",
        "you",
        "it",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "and",
        "or",
    }
    keywords = [w for w in re.findall(r"\b[a-zA-Z]{4,}\b", text) if w not in stop_words]
    memory_hints = ", ".join(keywords[:4]) if keywords else "none"

    return (
        f"[PARSED_INPUT]\n"
        f"intent: {intent}\n"
        f"tone: {tone}\n"
        f"complexity: {complexity}\n"
        f"entities: none\n"
        f"requires_tools: {str(requires_tools).lower()}\n"
        f"memory_hints: {memory_hints}\n"
        f"should_escalate: {str(should_escalate).lower()}\n"
    )


def preparse(user_input: str, habits: list, model: str = "") -> str:
    """
    Pre-parse user input via Ollama → PARSED_INPUT CSB block.
    Host and model selected dynamically via cluster_router at call time.
    Falls back to _rule_based_csb on timeout or error.
    Returns a CSB string (always — never raises).
    Logs fallback events to errors.log for telemetry (#30).
    """
    _client, _routed_model = _get_client_and_model("preparse")
    _model = model or _routed_model
    prompt = _PREPARSE_PROMPT.format(text=user_input[:300])
    fallback_reason = None
    t0 = time.perf_counter()
    try:
        _result_q: _queue.Queue = _queue.Queue()

        def _do_preparse():
            try:
                _result_q.put(
                    (
                        _client.chat(
                            model=_model,
                            messages=[{"role": "user", "content": prompt}],
                            options={"temperature": 0.1, "num_predict": 120},
                        ),
                        None,
                    )
                )
            except Exception as _e:
                _result_q.put((None, _e))

        _t = _threading.Thread(target=_do_preparse, daemon=True)
        _t.start()
        try:
            _resp, _err = _result_q.get(timeout=PREPARSE_TIMEOUT)
        except _queue.Empty:
            raise RuntimeError(f"preparse timed out after {PREPARSE_TIMEOUT}s")
        if _err:
            raise _err
        response = _resp
        elapsed = time.perf_counter() - t0
        text = (
            response["message"]["content"]
            if isinstance(response, dict)
            else response.message.content
        )
        _log_call("preparse", _model, response, elapsed)
        if "[PARSED_INPUT]" in text:
            return text.strip()
        fallback_reason = "no_parsed_input_block"
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        _log_call("preparse", _model, None, elapsed, error=str(exc))
        fallback_reason = f"exception:{type(exc).__name__}"

    try:
        log_error(
            kind="preparse_fallback",
            detail=fallback_reason or "unknown",
            source="ollama_reasoner",
        )
    except Exception as _bare_e:
        log_error(
            kind="BARE_EXCEPT",
            detail=f"wild_igor/igor/cognition/reasoners/ollama_reasoner.py: {_bare_e}",
        )

    return _rule_based_csb(user_input, habits)


def parse_preparse_csb(csb: str, habits: list) -> dict:
    """Extract routing dict from a PARSED_INPUT CSB block. Never raises."""
    fields: dict = {}
    for line in csb.splitlines():
        if ":" in line and not line.startswith("["):
            key, _, val = line.partition(":")
            fields[key.strip()] = val.strip()

    intent = fields.get("intent", "general")
    complexity = fields.get("complexity", "low")
    should_esc = fields.get("should_escalate", "false").lower() == "true"
    requires_tools = fields.get("requires_tools", "false").lower() == "true"

    score = {"low": 0.1, "medium": 0.4, "high": 0.8}.get(complexity, 0.1)
    is_multi_unit = score > 0.6 or requires_tools
    tier_minimum = "tier.4" if score > 0.4 else "tier.3"

    return {
        "intent": intent,
        "should_escalate": should_esc,
        "habit_match": None,
        "confidence": 0.0,
        "complexity": {
            "score": score,
            "signals_fired": [complexity] if complexity != "low" else [],
            "tier_minimum": tier_minimum,
            "is_multi_unit": is_multi_unit,
        },
        "_csb": csb,
    }


def _log_call(
    fn_name: str, model: str, response, elapsed: float, error: str | None = None
):
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

    tokens_in = getattr(response, "prompt_eval_count", None) or response.get(
        "prompt_eval_count", 0
    )
    tokens_out = getattr(response, "eval_count", None) or response.get("eval_count", 0)
    tok_per_sec = round(tokens_out / elapsed, 1) if elapsed > 0 and tokens_out else 0.0

    _ollama_log.info(
        f"fn={fn_name} model={model} elapsed_ms={elapsed*1000:.1f} "
        f"tokens_in={tokens_in} tokens_out={tokens_out} "
        f"tok_per_sec={tok_per_sec} ok=True"
    )


# ── Reasoner class ──────────────────────────────────────────────────────────


class OllamaReasoner(LocalReasoner, IgorBase):
    """Full reasoning via local or remote Ollama model. Slow but free.
    Host and model are resolved dynamically via cluster_router at each call."""

    def __init__(self, model=None, host=None):
        pass  # model/host ignored — resolved per-call via cluster_router

    @property
    def model(self) -> str:
        """Best current model from cluster_router (for logging)."""
        try:
            from ..cluster_router import route as _route

            _, m = _route("tier2")
            return m or DEFAULT_MODEL
        except Exception:
            return DEFAULT_MODEL

    def name(self) -> str:
        try:
            from ..cluster_router import route as _route

            host, model = _route("tier2")
            return f"Ollama/{model or DEFAULT_MODEL}@{host or 'localhost'}"
        except Exception:
            return f"Ollama/{DEFAULT_MODEL}@localhost"

    def reason(
        self,
        user_input: str,
        relevant_memories: list[Memory],
        core_patterns: list[Memory],
        instance_id: str,
        cortex=None,
        thread_id: str | None = None,
        force_local: bool = False,
        interactive_fallback: bool = False,
    ) -> tuple[str, float]:
        # Use role="analysis" system prompt when cortex is available (G57):
        # CP1-CP6 + brief identity — enough structure for local reasoning,
        # small enough that the 7B model can actually follow it.
        # Falls back to minimal hardcoded string if cortex is unavailable.
        if cortex is not None:
            try:
                system = build_system_prompt(cortex, role="analysis")
            except Exception:
                system = "Answer briefly and directly. Use the context provided. Say 'I don't know' when uncertain."
        else:
            system = "Answer briefly and directly. Use the context provided. Say 'I don't know' when uncertain."

        # D330: TWM-only context for tier.2 (minimal, fast)
        memory_context = ""
        try:
            from ..inference_gateway import build_twm_context

            twm_ctx = build_twm_context(
                cortex,
                tier="tier.2",
                thread_id=thread_id,
                relevant_memories=relevant_memories,
            )
            if twm_ctx:
                memory_context = "\n\n" + twm_ctx
        except Exception:
            # Fallback to legacy memory list
            if relevant_memories:
                memory_context = "\n\nRelevant memories:\n" + "\n".join(
                    f"- {m.narrative}" for m in relevant_memories[:5]
                )

        _query_chars = len(user_input)  # raw query before context append

        # Cap inputs to prevent OOM cascades on CPU-only inference (T-ollama-input-cap).
        # qwen2.5:7b at Q4 loads ~4.7 GB; large prompts saturate RAM and leave an
        # orphaned runner spinning at 300%+ CPU until manually killed.
        _MAX_USER_CHARS = int(os.getenv("IGOR_OLLAMA_MAX_USER_CHARS", "15000"))
        _MAX_CTX_CHARS = int(os.getenv("IGOR_OLLAMA_MAX_CTX_CHARS", "4000"))
        _MAX_SYS_CHARS = int(os.getenv("IGOR_OLLAMA_MAX_SYS_CHARS", "3000"))
        if len(user_input) > _MAX_USER_CHARS:
            log.warning(
                "OllamaReasoner: truncating user_input %d→%d chars (T-ollama-input-cap)",
                len(user_input),
                _MAX_USER_CHARS,
            )
            user_input = user_input[:_MAX_USER_CHARS]
        if len(memory_context) > _MAX_CTX_CHARS:
            log.warning(
                "OllamaReasoner: truncating memory_context %d→%d chars (T-ollama-input-cap)",
                len(memory_context),
                _MAX_CTX_CHARS,
            )
            memory_context = memory_context[:_MAX_CTX_CHARS]
        if len(system) > _MAX_SYS_CHARS:
            log.warning(
                "OllamaReasoner: truncating system %d→%d chars (T-ollama-input-cap)",
                len(system),
                _MAX_SYS_CHARS,
            )
            system = system[:_MAX_SYS_CHARS]

        _context_chars = len(system) + len(user_input) + len(memory_context)  # G55

        # Resolve host + model dynamically via cluster_router
        _client, _model = _get_client_and_model("tier2")

        # Cloud training mode: skip tier.2 Ollama — escalate to cloud (#CLOUD).
        # force_local=True bypasses this gate: background/impulse turns run as long
        # as needed regardless of cloud_mode (no interactive latency requirement).
        # interactive_fallback=True also bypasses the gate (cloud already failed)
        # but uses the full interactive timeout instead of the impulse timeout.
        if not force_local and not interactive_fallback:
            try:
                from ..cloud_mode import is_cloud_training_active as _cloud_active

                if _cloud_active():
                    raise RuntimeError("cloud_mode active — skip tier.2 Ollama")
            except RuntimeError:
                raise
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"wild_igor/igor/cognition/reasoners/ollama_reasoner.py: {_bare_e}",
                )

        # rule: theigors/rules/local-inference-no-timeouts — local takes
        # whatever time it takes; brain-modeled goal makes local-fast NOT a
        # constraint. Defaults are HOUR-scale sanity caps (catch a truly
        # hung Ollama process), NOT escalation triggers. The 90s default
        # this replaces fired 2026-05-03 20:17:27 and triggered the rule.
        # Env vars retained so operators can override per-machine if needed.
        # Interactive turns: 2hr (IGOR_OLLAMA_TIMEOUT_SECS=7200).
        # Impulse/background turns (force_local=True): 1hr (IGOR_OLLAMA_IMPULSE_TIMEOUT_SECS=3600).
        if force_local and not interactive_fallback:
            _timeout = float(os.getenv("IGOR_OLLAMA_IMPULSE_TIMEOUT_SECS", "3600"))
        else:
            _timeout = float(os.getenv("IGOR_OLLAMA_TIMEOUT_SECS", "7200"))

        _chat_q: _queue.Queue = _queue.Queue()

        def _do_chat():
            try:
                _chat_q.put(
                    (
                        _client.chat(
                            model=_model,
                            messages=[
                                {"role": "system", "content": system},
                                {
                                    "role": "user",
                                    "content": user_input + memory_context,
                                },
                            ],
                            keep_alive=-1,  # pin model in VRAM — no cold-start on next call
                        ),
                        None,
                    )
                )
            except Exception as _e:
                _chat_q.put((None, _e))

        t0 = time.perf_counter()
        try:
            _chat_thread = _threading.Thread(target=_do_chat, daemon=True)
            _chat_thread.start()
            try:
                _resp, _err = _chat_q.get(timeout=_timeout)
            except _queue.Empty:
                raise RuntimeError(
                    f"Ollama timed out after {_timeout}s — escalating to next tier"
                )
            if _err:
                raise _err
            response = _resp
            elapsed = time.perf_counter() - t0
            _log_call("OllamaReasoner.reason", _model, response, elapsed)
            tokens_in = getattr(response, "prompt_eval_count", None) or (
                response.get("prompt_eval_count", 0)
                if isinstance(response, dict)
                else 0
            )
            tokens_out = getattr(response, "eval_count", None) or (
                response.get("eval_count", 0) if isinstance(response, dict) else 0
            )
            try:
                from ..forensic_logger import log_reasoning_call as _lrc

                _lrc(
                    provider="ollama",
                    model=self.model,
                    tier="tier.2",
                    input_tokens=tokens_in,
                    output_tokens=tokens_out,
                    context_chars=_context_chars,
                    query_chars=_query_chars,
                    response_chars=len(
                        (response.get("message") or {}).get("content") or ""
                    ),
                    elapsed_ms=int(elapsed * 1000),
                )
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"wild_igor/igor/cognition/reasoners/ollama_reasoner.py: {_bare_e}",
                )
            text = response["message"]["content"]
            if not text or not text.strip():
                raise RuntimeError(
                    "Ollama returned blank response — escalating to next tier"
                )
            try:
                from ..forensic_logger import log_inference_io as _liio

                _prompt_sent = system + "\n\n" + user_input + memory_context
                _liio(
                    provider="ollama",
                    model=self.model,
                    tier="tier.2",
                    prompt=_prompt_sent,
                    response=text,
                    elapsed_ms=int(elapsed * 1000),
                    call_type="reason",
                )
            except Exception as _bare_e:
                log_error(
                    kind="BARE_EXCEPT",
                    detail=f"wild_igor/igor/cognition/reasoners/ollama_reasoner.py: {_bare_e}",
                )
            return text, 0.0  # Local = no cost
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            _log_call(
                "OllamaReasoner.reason", self.model, None, elapsed, error=str(exc)
            )
            raise


# ── preparse_dict (legacy; retained for reference) ──────────────────────────


def _preparse_dict(
    user_input: str, habits: list[Memory], model: str = DEFAULT_MODEL
) -> dict:
    """
    Legacy dict-returning preparse. Retained for reference only.
    Production preparse is now the CSB-format preparse() function above.
    """
    prompt = f"""Classify this user input. Reply with ONLY a JSON object, no other text.

User input: "{user_input}"

JSON fields:
- intent: one word from this list only: greeting, meta_question, factual_question, action_request, memory_instruction, general
- keywords: array of 2-4 important words from the input
- should_escalate: true if needs deep reasoning, false if simple

Example output:
{{"intent": "factual_question", "keywords": ["capital", "france"], "should_escalate": true}}"""

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

        return {
            "intent": parsed.get("intent", "general"),
            "keywords": parsed.get("keywords", []),
            "habit_match": None,
            "confidence": 0.0,
            "should_escalate": bool(parsed.get("should_escalate", True)),
        }

    except Exception as exc:
        elapsed = time.perf_counter() - t0
        _log_call("preparse", model, None, elapsed, error=str(exc))
        return {
            "intent": "general",
            "keywords": [],
            "habit_match": None,
            "confidence": 0.0,
            "should_escalate": True,
        }


# ── compute_complexity ──────────────────────────────────────────────────────

# Verbs that signal multi-step or heavy analytical work
_COMPLEX_VERBS = frozenset(
    {
        "ingest",
        "read",
        "analyze",
        "analyse",
        "build",
        "write",
        "generate",
        "review",
        "summarize",
        "summarise",
        "process",
        "parse",
        "extract",
        "compile",
        "iterate",
        "crawl",
        "scrape",
        "import",
    }
)

# Scope modifiers that indicate bulk/whole-collection tasks
_SCOPE_WORDS = frozenset(
    {
        "entire",
        "all",
        "every",
        "full",
        "complete",
        "whole",
    }
)

# Phrases/keywords that force escalation to tier.4 regardless of score
_FORCE_TIER4_PHRASES = (
    "use claude",
    "use sonnet",
    "use opus",
    "hard task",
    "difficult task",
    "complex task",
    "hard job",
)

# Tool-like action words (counting 3+ suggests multi-tool task)
_TOOL_KEYWORDS = frozenset(
    {
        "search",
        "read",
        "write",
        "send",
        "create",
        "delete",
        "fetch",
        "update",
        "list",
        "post",
        "get",
    }
)


def compute_complexity(user_input: str) -> dict:
    """
    Pure-Python complexity scoring for tier selection.

    Signals (additive):
      verb_match     +0.4  — contains a complex-task verb
      scope_match    +0.3  — contains a bulk/whole-collection scope word
      multi_step     +0.3  — 3+ chained actions (and/then/;)
      multi_tool     +0.2  — 3+ distinct tool-action words
      explicit       force — "use claude/sonnet/hard task" forces tier.4

    Thresholds:
      < 0.3   → tier.3 ok
      0.3-0.6 → tier.3 (check for self-escalation after 3 turns)
      > 0.6   → tier.4 minimum

    Returns dict with keys: score, signals_fired, tier_minimum,
                            force_tier4, is_multi_unit.
    """
    low = user_input.lower()
    signals: list[str] = []
    score = 0.0

    # Signal 1: complex-task verb
    if any(v in low for v in _COMPLEX_VERBS):
        signals.append("verb_match")
        score += 0.4

    # Signal 2: bulk scope word (must be a full token, not substring)
    tokens = set(low.split())
    if tokens & _SCOPE_WORDS:
        signals.append("scope_match")
        score += 0.3

    # Signal 3: multi-step chaining (3+ conjunctions/then clauses)
    chain_count = low.count(" and ") + low.count(", then") + low.count("; ")
    if chain_count >= 3:
        signals.append("multi_step")
        score += 0.3

    # Signal 4: multiple tool-type action words
    tool_hits = sum(1 for t in _TOOL_KEYWORDS if t in low)
    if tool_hits >= 3:
        signals.append("multi_tool")
        score += 0.2

    # Signal 5: explicit escalation request
    force = any(phrase in low for phrase in _FORCE_TIER4_PHRASES)
    if force:
        signals.append("explicit_escalate")

    score = min(1.0, round(score, 2))

    if force or score > 0.6:
        tier_minimum = "tier.4"
    else:
        tier_minimum = "tier.3"

    return {
        "score": score,
        "signals_fired": signals,
        "tier_minimum": tier_minimum,
        "force_tier4": force,
        # True when it looks like a bulk/multi-item job (used by job_manager trigger)
        "is_multi_unit": "scope_match" in signals and "verb_match" in signals,
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
    Compress ring memory into a dense CSB suitable for cold-reading by a future Igor.

    G22 / #22: improved prompt preserves open threads, decisions, and conversation
    context that the old prompt lost. Tries gpt-4o-mini (best quality, cheap) then
    Ollama batch model, then local 1B as last resort.
    """
    if not ring_entries:
        return f"SESSION_SUMMARY|{instance_id}|empty_session"

    # ── Separate conversation turns from system events ────────────────────────
    _NOISE = {
        "tool_trace",
        "interruptor",
        "latency_trace",
        "think_trace",
        "integrity_check",
        "action_impulse",
        "ne_diagnostic",
    }
    _CONVO = {"user_turn", "Q", "A"}

    convo_entries = [e for e in ring_entries if e.get("category") in _CONVO][-20:]
    system_entries = [
        e for e in ring_entries if e.get("category") not in _NOISE | _CONVO
    ][-20:]

    convo_text = (
        "\n".join(
            f"[{e['timestamp'][11:16]}] {e['content'][:300]}" for e in convo_entries
        )
        or "(no conversation turns)"
    )

    system_text = (
        "\n".join(
            f"[{e['timestamp'][11:16]}][{e['category']}] {e['content'][:150]}"
            for e in system_entries
        )
        or "(no system events)"
    )

    prompt = f"""You are Igor's memory compression system. Produce a dense session summary for cold-reading by a future Igor instance that has no other context.

CONVERSATION TURNS (what was discussed):
{convo_text}

SYSTEM EVENTS (habits, NE, decisions):
{system_text}

Fill in each field. Fragments only, no full sentences, no preamble. Max 25 words per field.

TASKS: <what was worked on or discussed, comma-separated>
CHANGES: <decisions, code changes, config changes made — be specific>
OPEN_THREADS: <unresolved topics or questions still in flight>
KEY_DECISIONS: <important conclusions reached this session>
NEXT_SESSION: <what to pick up next time>
STATE: <current state of any in-progress work>
VALENCE: <positive|neutral|negative>"""

    # ── Model preference: gpt-4o-mini → Ollama batch → local ─────────────────
    t0 = time.perf_counter()

    # Try gpt-4o-mini via OpenRouter first (much better at summarization than 1B)
    _or_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if _or_key:
        try:
            import json as _json
            import urllib.request as _req

            _payload = _json.dumps(
                {
                    "model": os.getenv("OPENROUTER_CHEAP_MODEL", "openai/gpt-4o-mini"),
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 400,
                    "temperature": 0.2,
                }
            ).encode()
            _http_req = _req.Request(
                "https://openrouter.ai/api/v1/chat/completions",
                data=_payload,
                headers={
                    "Authorization": f"Bearer {_or_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with _req.urlopen(_http_req, timeout=20) as resp:
                _data = _json.loads(resp.read())
            summary = _data["choices"][0]["message"]["content"].strip()
            elapsed = time.perf_counter() - t0
            _log_call("summarize_session", "gpt-4o-mini", None, elapsed)
            return f"SESSION_SUMMARY|{instance_id}|{summary}"
        except Exception as _bare_e:
            log_error(
                kind="BARE_EXCEPT",
                detail=f"wild_igor/igor/cognition/reasoners/ollama_reasoner.py: {_bare_e}",
            )

    # Try Ollama batch model (qwen2.5:14b — better reasoning than 1B)
    _batch_model = os.getenv("OLLAMA_BATCH_MODEL", "qwen2.5:14b")
    for _model in (_batch_model, DEFAULT_MODEL):
        try:
            response = _ollama.chat(
                model=_model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.2, "num_predict": 400},
            )
            elapsed = time.perf_counter() - t0
            _log_call("summarize_session", _model, response, elapsed)
            summary = (
                response["message"]["content"]
                if isinstance(response, dict)
                else response.message.content
            ).strip()
            return f"SESSION_SUMMARY|{instance_id}|{summary}"
        except Exception:
            continue

    # Last resort: manual join
    elapsed = time.perf_counter() - t0
    _log_call("summarize_session", "fallback", None, elapsed, error="all models failed")
    lines = [e["content"][:120] for e in (convo_entries + system_entries)[-5:]]
    return f"SESSION_SUMMARY|{instance_id}|fallback: " + " | ".join(lines)
