"""Librarian research and summarization engine.

Provides summarize(), research(), and build_summary() backed by the
InferenceRouter tier system. All LLM calls are injectable so callers
(and tests) can swap in a stub without live model services.

Usage:
    engine = ResearchEngine()
    result = engine.summarize("Some long text...", style="brief")
    result = engine.research("what is IMAP IDLE?", depth="shallow")
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Callable

from agent_datacenter.devices.librarian.inference import InferenceRouter, ModelSelection

log = logging.getLogger(__name__)

LLMCallable = Callable[[ModelSelection, str], str]


# ── Default LLM backend ───────────────────────────────────────────────────────


def _call_ollama(selection: ModelSelection, prompt: str) -> str:
    import urllib.request

    payload = json.dumps(
        {"model": selection.model, "prompt": prompt, "stream": False}
    ).encode()
    req = urllib.request.Request(
        "http://localhost:11434/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read())
        return data.get("response", "")


def _call_anthropic(selection: ModelSelection, prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=selection.model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text if msg.content else ""


def default_llm_call(selection: ModelSelection, prompt: str) -> str:
    """Route to the appropriate backend based on ModelSelection."""
    if selection.backend == "anthropic":
        return _call_anthropic(selection, prompt)
    return _call_ollama(selection, prompt)


# ── Results ───────────────────────────────────────────────────────────────────


@dataclass
class SummarizeResult:
    text: str
    style: str
    model: str
    tier: int
    char_count_in: int


@dataclass
class ResearchResult:
    query: str
    depth: str
    answer: str
    model: str
    tier: int
    sources: list[str] = field(default_factory=list)


# ── Engine ────────────────────────────────────────────────────────────────────


class ResearchEngine:
    """Research and summarization backed by InferenceRouter tier selection.

    llm_call is injectable for testing. Defaults to default_llm_call which
    tries ollama (local) or anthropic (cloud) per the ModelSelection backend.
    """

    def __init__(
        self,
        router: InferenceRouter | None = None,
        llm_call: LLMCallable | None = None,
    ) -> None:
        self._router = router or InferenceRouter()
        self._llm_call = llm_call or default_llm_call

    def summarize(self, text: str, style: str = "brief") -> SummarizeResult:
        """Summarize text. style: 'brief' | 'detailed' | 'bullets'."""
        if not text or not text.strip():
            raise ValueError("summarize: text must be non-empty")

        style_instructions = {
            "brief": "Summarize in 2-3 sentences.",
            "detailed": "Write a detailed summary covering all key points.",
            "bullets": "Summarize as a bullet list of key points (5-10 bullets).",
        }
        instruction = style_instructions.get(style, style_instructions["brief"])
        prompt = f"{instruction}\n\nText:\n{text[:8000]}"

        selection = self._router.select(task_type="summarize")
        result = self._llm_call(selection, prompt)

        return SummarizeResult(
            text=result,
            style=style,
            model=selection.model,
            tier=selection.tier,
            char_count_in=len(text),
        )

    def research(self, query: str, depth: str = "shallow") -> ResearchResult:
        """Research a query. depth: 'shallow' (direct answer) | 'deep' (multi-step)."""
        if not query or not query.strip():
            raise ValueError("research: query must be non-empty")

        if depth == "deep":
            return self._research_deep(query)
        return self._research_shallow(query)

    def build_summary(self, topic: str) -> SummarizeResult:
        """Build a summary for a topic or ticket ID. Treated as a summarize call."""
        prompt_text = f"Topic or ticket: {topic}\n\nSummarize what is known about this topic based on the identifier."
        return self.summarize(prompt_text, style="brief")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _research_shallow(self, query: str) -> ResearchResult:
        prompt = (
            f"Answer the following question clearly and concisely. "
            f"If you don't know, say so.\n\nQuestion: {query}"
        )
        selection = self._router.select(task_type="research")
        answer = self._llm_call(selection, prompt)
        return ResearchResult(
            query=query,
            depth="shallow",
            answer=answer,
            model=selection.model,
            tier=selection.tier,
        )

    def _research_deep(self, query: str) -> ResearchResult:
        # Deep research: synthesize → answer. Full search+fetch requires external
        # tools not yet wired; this provides the synthesis step.
        prompt = (
            f"Provide a thorough, structured answer to the following question. "
            f"Include relevant context, caveats, and examples where helpful.\n\n"
            f"Question: {query}"
        )
        selection = self._router.select(task_type="research")
        answer = self._llm_call(selection, prompt)
        return ResearchResult(
            query=query,
            depth="deep",
            answer=answer,
            model=selection.model,
            tier=selection.tier,
            sources=[],  # populated when external search is wired
        )
