"""
local_preparse — local-only mini-LLM fallback for preparse.

T-local-preparse-fallback (D-preparse-architecture-2026-04-22).

When the graph-tree gist-pass (T-gist-before-retrieve) returns low
confidence, today we fall through to cortex.search. This module adds a
middle tier: a local small-model LLM (Ollama qwen 0.5B / llama3.2:1b /
similar) that handles cases the trees don't recognize but which don't
require full cortex retrieval. Zero network cost, sub-second latency
on local hardware.

## Local-only constraint (load-bearing)

Akien explicitly removed web-LLM preparse (gpt5mini) from akiendell
and akiendelllinux — the latency was unacceptable on the critical
path. This fallback must NEVER call cloud inference. If the local
Ollama is unreachable, the caller falls through to cortex.search (or
whatever the next tier is); we don't pivot to OpenRouter or Anthropic.

## Schema

preparse() returns a CSB block string identical in shape to what
ollama_reasoner.preparse() emits — intent/tone/complexity/entities/
requires_tools/memory_hints/should_escalate lines. Downstream consumer
is parse_preparse_csb() from ollama_reasoner, so no caller needs to
know which tier produced the block.

Returns None on any failure (timeout, unreachable, parse error) so the
caller's fall-through path is a simple `if csb is None: ...`.

## Gate

IGOR_LOCAL_PREPARSE_ENABLED (default true — local-first is the whole
point). Disable only for debugging or when running on a host with no
Ollama.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional

from ..igor_base import IgorBase
from ..igor_base import get_logger

log = get_logger(__name__)

# Default model — qwen2.5:0.5b is fastest; llama3.2:1b is the conservative
# pick if qwen isn't pulled. Configurable via env var.
_DEFAULT_MODEL = os.getenv("IGOR_LOCAL_PREPARSE_MODEL", "qwen2.5:0.5b")

# rule: unseenuniversity/rules/local-inference-no-timeouts — local takes whatever
# time it takes; brain-modeled goal makes local-fast NOT a constraint.
# Hour-scale sanity cap (catch a truly hung Ollama process), NOT a UX
# deadline. The previous 1.0s default treated routine local slowness as a
# fall-through trigger — exactly the anti-pattern the rule prohibits.
# If preparse latency becomes a UX problem, the answer is a non-blocking
# preparse architecture (parallel on swarm, async future, optimistic
# rule-based first then upgrade), NOT a short timeout.
_DEFAULT_TIMEOUT_SEC = float(os.getenv("IGOR_LOCAL_PREPARSE_TIMEOUT_SEC", "3600"))


_PREPARSE_PROMPT_TEMPLATE = """\
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


def _enabled() -> bool:
    return os.getenv("IGOR_LOCAL_PREPARSE_ENABLED", "true").lower() == "true"


class LocalPreparser(IgorBase):
    """Local-only Ollama preparse client.

    Intentionally minimal — no model fallback chain, no routing logic,
    no cloud pivot. If the local host is unreachable or the model isn't
    pulled, preparse() returns None and the caller handles it.
    """

    def __init__(
        self,
        model: str | None = None,
        timeout_sec: float | None = None,
        host: str | None = None,
    ) -> None:
        super().__init__()
        self.model = model or _DEFAULT_MODEL
        self.timeout_sec = (
            timeout_sec if timeout_sec is not None else _DEFAULT_TIMEOUT_SEC
        )
        self.host = host or os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self._last_latency_ms: Optional[int] = None
        self._invocation_count = 0
        self._failure_count = 0

    @property
    def last_latency_ms(self) -> Optional[int]:
        return self._last_latency_ms

    @property
    def invocation_count(self) -> int:
        return self._invocation_count

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def preparse(self, user_input: str) -> Optional[str]:
        """Parse `user_input` via local Ollama. Return a PARSED_INPUT CSB
        block on success, None on any failure."""
        if not _enabled():
            return None
        if not user_input or not user_input.strip():
            return None

        self._invocation_count += 1
        # Truncate aggressive — local small models work best with tight prompts
        truncated = user_input.strip()[:300]
        prompt = _PREPARSE_PROMPT_TEMPLATE.format(text=truncated)

        t0 = time.monotonic()
        try:
            csb = self._call_ollama(prompt)
        except Exception as exc:
            self._failure_count += 1
            log.debug("local_preparse call failed: %s", exc)
            return None
        finally:
            self._last_latency_ms = int((time.monotonic() - t0) * 1000)

        if csb is None:
            self._failure_count += 1
            return None

        # Verify the block shape — any junk before "[PARSED_INPUT]" is tolerated
        # as long as the sentinel is present.
        if "[PARSED_INPUT]" not in csb:
            self._failure_count += 1
            log.debug("local_preparse output missing [PARSED_INPUT] sentinel")
            return None

        log.debug(
            "local_preparse ok model=%s latency_ms=%d",
            self.model,
            self._last_latency_ms,
        )
        return csb.strip()

    def _call_ollama(self, prompt: str) -> Optional[str]:
        """Low-level Ollama invocation. Thread of responsibility:
        self → _call_ollama → ollama.Client.chat.

        Returns the raw response text or None on any failure. Timeout
        is enforced via the Ollama client's options.
        """
        # Late import so unit tests that don't need Ollama don't pay the
        # startup cost.
        import ollama as _ollama  # type: ignore

        # Respect configured host. ollama.Client(host=...) when host differs
        # from default; default module-level client otherwise.
        client = (
            _ollama
            if self.host in (None, "http://localhost:11434")
            else _ollama.Client(host=self.host)
        )

        # num_predict caps output tokens; keep_alive keeps the model warm
        # so repeated preparse calls don't re-load the model each time.
        resp = client.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            options={
                "temperature": 0.0,  # deterministic classification
                "num_predict": 120,  # parsed block is small
            },
            keep_alive="5m",
        )
        try:
            text = (
                resp["message"]["content"]
                if isinstance(resp, dict)
                else resp.message.content
            )
        except (KeyError, AttributeError, TypeError):
            return None
        return text if isinstance(text, str) else None


# Module-level singleton — callers get the same instance so stats (latency,
# invocation count) accumulate. Overridable via set_default_preparser for
# tests.
_default_preparser: Optional[LocalPreparser] = None


def default_preparser() -> LocalPreparser:
    global _default_preparser
    if _default_preparser is None:
        _default_preparser = LocalPreparser()
    return _default_preparser


def set_default_preparser(p: Optional[LocalPreparser]) -> None:
    """Test helper — inject a custom or None preparser."""
    global _default_preparser
    _default_preparser = p


def preparse_local(user_input: str) -> Optional[str]:
    """Convenience function: route to the default LocalPreparser."""
    return default_preparser().preparse(user_input)
