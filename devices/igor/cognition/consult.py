"""consult.py — peer-LLM consultation primitive (D-consult-primitive-2026-04-23).

Igor asks a peer-LLM "help me understand what's wrong — do not solve" when
stuck at a reasoning or coding decision point. The LLM returns ranked
hypotheses + one question most likely to unstick Igor, as JSON. Igor reasons
over the result and may follow up (multi-turn session) or conclude.

Core distinction from the old "LLM escalation" shape: Igor stays the agent.
The LLM is a peer consultant, never the answerer. Prompt templates force the
register (see consult_prompts.py).

Usage:
    from wild_igor.igor.cognition.consult import consult, ConsultSession, ConsultState

    state = ConsultState(
        problem_kind="coding",
        summary="pe_chain SITUATE returned 0 files for T-foo",
        what_i_tried="ran qwen-2.5-coder at temp=0.1",
        what_failed="post-filter dropped all results as HIGH-inertia",
        extra={"ticket_desc": "...", "pe_chain_tail": "..."},
    )
    # one-shot helper:
    result = consult(state, "What am I missing about the post-filter?")

    # multi-turn session:
    session = ConsultSession(state)
    r1 = session.ask("What am I missing about the post-filter?")
    # reason over r1.hypotheses ...
    r2 = session.ask("Is the kernel.py blocklist too tight?")
    conclusion = session.conclude()

Transport: OpenRouter via OPENROUTER_API_KEY. Tier default = tier.3 (cheap
cloud). Override with tier_override="tier.3.5" for harder consults.

Logging:
- igor.consult logger at INFO — console-visible; does NOT broadcast to web
- Forensic log file ~/.TheIgors/local/logs/consults.log — one JSON line per turn
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from ..igor_base import IgorBase

log = logging.getLogger("igor.consult")

# Tier → model. Defaults kept qwen-family for coding consults, claude-haiku for
# reasoning consults (matching the rest of Igor's routing philosophy).
_TIER_MODELS = {
    "tier.3": os.getenv("IGOR_CONSULT_TIER3_MODEL", "anthropic/claude-haiku-4.5"),
    "tier.3.5": os.getenv("IGOR_CONSULT_TIER35_MODEL", "anthropic/claude-haiku-4.5"),
    "tier.4": os.getenv("IGOR_CONSULT_TIER4_MODEL", "anthropic/claude-sonnet-4.6"),
}
DEFAULT_TIER = "tier.3"

CONSULT_LOG_PATH = Path(
os.getenv('IGOR_TEST_MODE', '') and 
    os.getenv('IGOR_TEST_MODE', '') and os.getenv(
        "IGOR_CONSULT_LOG",
        os.getenv('IGOR_TEST_MODE', '') and str(Path.home() / ".TheIgors" / "local" / "logs" / "consults.log.test") or str(Path.home() / ".TheIgors" / "local" / "logs" / "consults.log"),
    )
)

_LOG_LOCK = threading.Lock()


# Inline default prompt templates. T-consult-prompts replaces these with
# richer per-problem-kind templates in consult_prompts.py. Kept here so
# T-consult-primitive stands alone with no sibling-ticket dependency.
_DEFAULT_SYSTEM_PROMPT = (
    "You are a peer consultant. I am Igor, a graph matrix reasoning engine. "
    "I am stuck. Help me understand what is wrong — DO NOT SOLVE. Return a "
    "JSON object with three fields:\n"
    "  hypotheses: array of up to 3 short strings, ranked most-likely first\n"
    "  next_question: a single question most likely to unstick me\n"
    "  confidence: number in [0, 1] — your self-assessment\n"
    "Do not generate code. Do not write replies on my behalf. Frame "
    "suggestions as questions. Respond with JSON only."
)


def _build_default_state_message(state: "ConsultState") -> str:
    lines = [f"problem_kind: {state.problem_kind}", f"summary: {state.summary}"]
    if state.what_i_tried:
        lines.append(f"what_i_tried: {state.what_i_tried}")
    if state.what_failed:
        lines.append(f"what_failed: {state.what_failed}")
    if state.ticket_id:
        lines.append(f"ticket_id: {state.ticket_id}")
    if state.pursuit_id:
        lines.append(f"pursuit_id: {state.pursuit_id}")
    if state.extra:
        for k, v in state.extra.items():
            lines.append(f"{k}: {str(v)[:800]}")
    return "\n".join(lines)


ProblemKind = Literal["reasoning", "coding"]


@dataclass
class ConsultState:
    """Input bundle to a consult session. Shape common across problem kinds;
    extra dict carries per-kind context (ticket_desc + pe_chain_tail for
    coding; user_turn + thread_excerpt + twm_topk for reasoning)."""

    problem_kind: ProblemKind
    summary: str  # one-line: what's stuck
    what_i_tried: str = ""  # recent attempts
    what_failed: str = ""  # the error / unexpected outcome
    pursuit_id: Optional[str] = None  # active pursuit if any
    ticket_id: Optional[str] = None
    extra: dict = field(default_factory=dict)


@dataclass
class ConsultResult:
    """Per-turn consult output."""

    hypotheses: list[str]  # ranked, up to 3
    next_question: str  # single best follow-up question
    confidence: float  # 0.0–1.0
    raw_text: str  # full LLM response for audit + confab scan
    turn_idx: int  # 0-based, within session
    cost_usd: float  # per-turn cost (0.0 if not known)
    elapsed_ms: int
    # T-consult-confab-scan: list of confab tell matches in raw_text.
    # Populated by ask() running confab_scanner before returning the result.
    # Empty list = clean. Callers can log / downweight based on len > 0.
    # v1: detection only — salience halving deferred to T-consult-observe-and-tune
    # once we have data on false-positive rates.
    confab_flags: list[str] = field(default_factory=list)


@dataclass
class ConsultConclusion:
    """Returned by session.conclude() — summary across all turns."""

    final_hypothesis: str  # best-of across turns
    confidence: float
    turn_count: int
    total_cost: float
    transcript: list[ConsultResult]  # full turn history


def _log_forensic(session_id: str, entry: dict) -> None:
    """Append one JSON line to the consults forensic log. Non-fatal."""
    entry = {"session_id": session_id, **entry}
    try:
        CONSULT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK:
            with open(CONSULT_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.debug("consult forensic log write failed (non-fatal): %s", exc)


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _new_session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def _parse_json_reply(raw: str) -> tuple[list[str], str, float]:
    """Parse the LLM's JSON reply into (hypotheses, next_question, confidence).

    Tolerates minor drift: extra prose before/after the JSON, code-fence
    wrapping. Raises ValueError if the required fields are missing.
    """
    text = raw.strip()
    # Strip common code-fence wrappers
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Find first { and last } and slice — tolerates prose wrapping the JSON
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object in consult reply: {raw[:120]!r}")
    obj = json.loads(text[start : end + 1])

    hypotheses = obj.get("hypotheses", [])
    if not isinstance(hypotheses, list):
        raise ValueError("hypotheses is not a list")
    hypotheses = [str(h).strip() for h in hypotheses if h][:3]

    next_q = str(obj.get("next_question", "")).strip()
    if not next_q:
        raise ValueError("next_question missing")

    try:
        confidence = float(obj.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return hypotheses, next_q, confidence


def _call_openrouter(
    messages: list[dict], model: str, timeout_s: float = 60.0
) -> tuple[str, int]:
    """Thin OR call. Returns (raw_text, elapsed_ms). Raises on failure."""
    or_key = os.getenv("OPENROUTER_API_KEY", "")
    if not or_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    t0 = time.monotonic()
    payload = json.dumps(
        {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": 0.2,  # consult wants measured, not creative
            # Response-format JSON for providers that support it — the prompt
            # also instructs JSON so we're belt + suspenders.
            "response_format": {"type": "json_object"},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {or_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read())
    text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
    elapsed_ms = int((time.monotonic() - t0) * 1000)
    return text, elapsed_ms


class ConsultSession(IgorBase):
    """Multi-turn consult session. State accumulates across turns.

    tier_override: "tier.3" | "tier.3.5" | "tier.4" — defaults to DEFAULT_TIER.
    """

    def __init__(
        self,
        state: ConsultState,
        tier_override: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.state = state
        self.tier = tier_override or DEFAULT_TIER
        if self.tier not in _TIER_MODELS:
            raise ValueError(
                f"unknown tier {self.tier!r} — expected one of {list(_TIER_MODELS)}"
            )
        self.model = _TIER_MODELS[self.tier]
        self.session_id = _new_session_id()
        self.transcript: list[ConsultResult] = []
        self._messages: list[dict] = self._initial_messages()

        log.info(
            "consult opened session=%s kind=%s tier=%s model=%s pursuit=%s ticket=%s",
            self.session_id,
            state.problem_kind,
            self.tier,
            self.model,
            state.pursuit_id,
            state.ticket_id,
        )
        _log_forensic(
            self.session_id,
            {
                "event": "session_open",
                "ts": _now_iso(),
                "problem_kind": state.problem_kind,
                "tier": self.tier,
                "model": self.model,
                "summary": state.summary,
                "ticket_id": state.ticket_id,
                "pursuit_id": state.pursuit_id,
            },
        )

    def _initial_messages(self) -> list[dict]:
        # Lazy import with stub fallback: T-consult-prompts will enrich the
        # templates; until then we use inline defaults so T-consult-primitive
        # is independently testable.
        try:
            from .consult_prompts import build_system_prompt, build_state_message

            return [
                {
                    "role": "system",
                    "content": build_system_prompt(self.state.problem_kind),
                },
                {"role": "user", "content": build_state_message(self.state)},
            ]
        except ImportError:
            return [
                {"role": "system", "content": _DEFAULT_SYSTEM_PROMPT},
                {"role": "user", "content": _build_default_state_message(self.state)},
            ]

    def ask(self, question: str) -> ConsultResult:
        """One consult turn. Appends the question, calls the LLM, parses JSON,
        records to transcript + forensic log, returns ConsultResult.
        """
        self._messages.append({"role": "user", "content": question})
        raw_text = ""
        elapsed_ms = 0
        try:
            raw_text, elapsed_ms = _call_openrouter(self._messages, self.model)
        except Exception as exc:
            log.warning(
                "consult ask failed session=%s turn=%d: %s",
                self.session_id,
                len(self.transcript),
                exc,
            )
            _log_forensic(
                self.session_id,
                {
                    "event": "ask_error",
                    "ts": _now_iso(),
                    "turn_idx": len(self.transcript),
                    "error": str(exc),
                },
            )
            # Return empty-shell result rather than raising — caller can check
            # confidence==0 and move on without consult input.
            result = ConsultResult(
                hypotheses=[],
                next_question="",
                confidence=0.0,
                raw_text=f"<error: {exc}>",
                turn_idx=len(self.transcript),
                cost_usd=0.0,
                elapsed_ms=0,
            )
            self.transcript.append(result)
            return result

        # Parse JSON reply; on parse failure log + return empty-shell
        try:
            hypotheses, next_q, conf = _parse_json_reply(raw_text)
        except Exception as exc:
            log.warning(
                "consult parse failed session=%s: %s — raw=%r",
                self.session_id,
                exc,
                raw_text[:200],
            )
            _log_forensic(
                self.session_id,
                {
                    "event": "parse_error",
                    "ts": _now_iso(),
                    "turn_idx": len(self.transcript),
                    "error": str(exc),
                    "raw_text": raw_text[:500],
                },
            )
            result = ConsultResult(
                hypotheses=[],
                next_question="",
                confidence=0.0,
                raw_text=raw_text,
                turn_idx=len(self.transcript),
                cost_usd=0.0,
                elapsed_ms=elapsed_ms,
            )
            self.transcript.append(result)
            return result

        # Append assistant turn so next ask() accumulates context
        self._messages.append({"role": "assistant", "content": raw_text})

        # T-consult-confab-scan: run confab_scanner on raw_text before caller
        # integrates. Detection-only — flagged results still go into the
        # result (and transcript), callers can log/downweight. v1 keeps the
        # behavior neutral; T-consult-observe-and-tune reviews whether to
        # halve salience or drop based on observed false-positive rate.
        confab_flags: list = []
        try:
            # Scanner lives in lab.claudecode.engram_tools (CC-side toolkit)
            from lab.claudecode.engram_tools.confab_scanner import scan_turns

            confab_flags = scan_turns(
                [
                    {
                        "turn_id": f"{self.session_id}-t{len(self.transcript)}",
                        "out": raw_text,
                    }
                ]
            )
        except Exception as scan_exc:
            log.debug("consult confab scan failed (non-fatal): %s", scan_exc)

        result = ConsultResult(
            hypotheses=hypotheses,
            next_question=next_q,
            confidence=conf,
            raw_text=raw_text,
            turn_idx=len(self.transcript),
            cost_usd=0.0,  # TODO: populate from OR response when we surface it
            elapsed_ms=elapsed_ms,
            confab_flags=confab_flags,
        )
        self.transcript.append(result)

        if confab_flags:
            log.info(
                "consult confab flagged session=%s turn=%d n=%d subtypes=%s",
                self.session_id,
                result.turn_idx,
                len(confab_flags),
                sorted({m.subtype for m in confab_flags}),
            )
            _log_forensic(
                self.session_id,
                {
                    "event": "confab_flag",
                    "ts": _now_iso(),
                    "turn_idx": result.turn_idx,
                    "flags": [
                        {
                            "subtype": m.subtype,
                            "confidence": m.confidence,
                            "tell_phrase": m.tell_phrase,
                        }
                        for m in confab_flags
                    ],
                },
            )

        log.info(
            "consult turn session=%s turn=%d conf=%.2f elapsed=%dms hyps=%d q=%r",
            self.session_id,
            result.turn_idx,
            result.confidence,
            result.elapsed_ms,
            len(result.hypotheses),
            result.next_question[:60],
        )
        _log_forensic(
            self.session_id,
            {
                "event": "ask_ok",
                "ts": _now_iso(),
                "turn_idx": result.turn_idx,
                "confidence": result.confidence,
                "elapsed_ms": result.elapsed_ms,
                "hypotheses": result.hypotheses,
                "next_question": result.next_question,
                "raw_text": result.raw_text[:2000],
            },
        )
        return result

    def conclude(self) -> ConsultConclusion:
        """Close the session and return a summary.

        Picks the best hypothesis as the final — highest-confidence turn's
        top-1. Caller may ignore and synthesize differently, but this is the
        default roll-up.
        """
        if not self.transcript:
            conclusion = ConsultConclusion(
                final_hypothesis="",
                confidence=0.0,
                turn_count=0,
                total_cost=0.0,
                transcript=[],
            )
        else:
            best = max(self.transcript, key=lambda r: r.confidence)
            final = best.hypotheses[0] if best.hypotheses else ""
            conclusion = ConsultConclusion(
                final_hypothesis=final,
                confidence=best.confidence,
                turn_count=len(self.transcript),
                total_cost=sum(r.cost_usd for r in self.transcript),
                transcript=list(self.transcript),
            )

        log.info(
            "consult closed session=%s turns=%d conf=%.2f final=%r",
            self.session_id,
            conclusion.turn_count,
            conclusion.confidence,
            conclusion.final_hypothesis[:80],
        )
        _log_forensic(
            self.session_id,
            {
                "event": "session_close",
                "ts": _now_iso(),
                "turn_count": conclusion.turn_count,
                "confidence": conclusion.confidence,
                "final_hypothesis": conclusion.final_hypothesis,
                "total_cost": conclusion.total_cost,
            },
        )
        return conclusion


def consult(
    state: ConsultState,
    question: str,
    tier_override: Optional[str] = None,
) -> ConsultResult:
    """One-shot helper: open a session, ask once, return the result.

    For callers that don't need multi-turn. The session is closed (conclusion
    logged) internally — the ConsultResult is still the return value since
    that's what callers want.
    """
    session = ConsultSession(state, tier_override=tier_override)
    result = session.ask(question)
    session.conclude()
    return result
