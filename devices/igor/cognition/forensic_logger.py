import sys

"""
Forensic Logger — WO3: Cognition Stabilization Phase 3.

CSB-format structured logs at ~/.TheIgors/logs/ for post-mortem analysis.
All logs are newest-first (prepend, not append). Rotate at 10MB.

Log files:
    reasoning_calls.log  — every cloud/local inference API call (anthropic, openrouter, ollama)
    ne_runs.log          — every Narrative Engine run
    self_edit.log        — every self-edit attempt (allowed or blocked)
    tool_calls.log       — every tool invocation (high-volume; optional)
    memory_ops.log       — memory store/search operations (optional)
    errors.log           — runtime errors: impulse skips, tier failures, degraded-mode events
    escalation.log       — per-turn routing decisions: why did we reach up? (G37 weaning data)

All functions are fire-and-forget: exceptions are swallowed so logging
can never crash the main loop.
"""

import json as _json
import threading as _threading
from datetime import datetime
from pathlib import Path

from ..paths import paths

LOG_DIR = paths().logs
MAX_BYTES = 10 * 1024 * 1024  # 10 MB

# ── Per-turn state (threading.local) ──────────────────────────────────────────
# _current_turn.id  — turn_id string, set at start of _process_inner()
# _current_turn.ctx — TurnContext dict (#203), built up as pipeline runs
# Both live on the same threading.local so worker threads (#200) each get
# their own independent state with zero locking overhead.
_current_turn = _threading.local()


def set_turn_id(tid: str) -> None:
    """Record the active turn ID for pipeline trace logging."""
    _current_turn.id = tid


def get_turn_id() -> str:
    """Return the active turn ID, or '?' if not set."""
    return getattr(_current_turn, "id", "?")


# ── TurnContext (#203) ─────────────────────────────────────────────────────────
# Accumulates pipeline state into a dict so the entire cognition path for one
# turn is visible as a single structured object in turn_trace.YYYYMMDD.log.


def init_turn_ctx(turn_id: str, thread_id: str, input_text: str) -> None:
    """Start a fresh TurnContext for this turn on the current thread."""
    _current_turn.ctx = {
        "turn_id": turn_id,
        "thread_id": thread_id or "stdin:main",
        "ts": _ts(),
        "input": input_text[:300],
    }


def turn_ctx_update(stage: str, data: dict) -> None:
    """Write one stage's data into the current TurnContext. Safe to call anywhere."""
    ctx = getattr(_current_turn, "ctx", None)
    if ctx is None:
        return
    ctx[stage] = {
        k: (round(v, 4) if isinstance(v, float) else v) for k, v in data.items()
    }


def finalize_turn_ctx(
    *,
    response_preview: str = "",
    tier: str = "",
    cost_usd: float = 0.0,
    total_ms: int = 0,
    new_memories: int = 0,
    habit_fired: bool = False,
) -> None:
    """
    Close the TurnContext, add response summary, and append to turn_trace log.
    Called from _process_inner() finally: block.
    Gate: IGOR_TURN_TRACE (default true).
    """
    try:
        import os as _os

        if _os.getenv("IGOR_TURN_TRACE", "true").lower() in ("0", "false", "no"):
            return

        ctx = getattr(_current_turn, "ctx", None)
        if ctx is None:
            return

        ctx["response"] = {
            "preview": response_preview[:200].replace("\n", " "),
            "tier": tier,
            "cost_usd": round(cost_usd, 5),
            "new_memories": new_memories,
            "habit_fired": habit_fired,
            "total_ms": total_ms,
        }

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        path = LOG_DIR / f"turn_trace.{today}.log"
        entry = (
            f"\n=== turn {ctx.get('turn_id','?')} | {ctx.get('thread_id','?')}"
            f" | {ctx.get('ts','?')} | {total_ms}ms total ===\n"
            + _json.dumps(ctx, indent=2)
            + "\n=== END ===\n"
        )
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)

        _purge_old_turn_traces(today)
    except Exception as _bare_e:
        sys.stderr.write(
            f"[forensic_logger] bare except in wild_igor/igor/cognition/forensic_logger.py: {_bare_e}\n"
        )
    finally:
        _current_turn.ctx = None


def _purge_old_turn_traces(today: str) -> None:
    """Delete turn_trace logs older than 2 days."""
    try:
        from datetime import datetime as _dt, timedelta

        cutoff = (_dt.strptime(today, "%Y%m%d") - timedelta(days=2)).strftime("%Y%m%d")
        for p in LOG_DIR.glob("turn_trace.*.log"):
            date_part = p.stem.split(".")[-1]
            if date_part.isdigit() and date_part < cutoff:
                p.unlink(missing_ok=True)
    except Exception as _bare_e:
        sys.stderr.write(
            f"[forensic_logger] bare except in wild_igor/igor/cognition/forensic_logger.py: {_bare_e}\n"
        )


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def cts() -> str:
    """Short console timestamp prefix: 'HHmmss ' — prepend to diagnostic prints."""
    return datetime.now().strftime("%H%M%S ")


def _prepend(log_name: str, entry: str) -> None:
    """Prepend one CSB line to a log file. Rotate to .old if > 10 MB."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / log_name
        if path.exists():
            if path.stat().st_size > MAX_BYTES:
                old = path.with_suffix(".old")
                if old.exists():
                    old.unlink()
                path.rename(old)
                existing = ""
            else:
                existing = path.read_text(encoding="utf-8")
        else:
            existing = ""
        path.write_text(entry + "\n" + existing, encoding="utf-8")
    except Exception as _bare_e:
        sys.stderr.write(
            f"[forensic_logger] bare except in wild_igor/igor/cognition/forensic_logger.py: {_bare_e}\n"
        )


# ── Public log functions ──────────────────────────────────────────────────────


def log_reasoning_call(
    *,
    provider: str,  # "anthropic" | "openrouter" | "ollama"
    model: str,
    tier: str = "",  # "tier.2" | "tier.3" | "tier.3.5" | "tier.4" | "tier.5"
    input_tokens: int = 0,
    output_tokens: int = 0,
    context_chars: int = 0,  # chars of context passed in (system + user + memories)
    query_chars: int = 0,  # chars of raw user query only (before context append)
    response_chars: int = 0,  # chars of response produced
    cost_usd: float = 0.0,
    elapsed_ms: int = 0,
    turns: int = 1,
    response_summary: str = "",
    escalation_reason: str = "",
) -> None:
    """Log one inference API call — cloud or local (full tool-loop, not per-turn).

    context_chars: total chars of context passed to the model on turn 1.
    query_chars: chars of the raw user query before any context is appended.
      Training signal: what question drove this cloud call, independent of memory load.
    response_chars: chars of the response produced.
      Training signal: how complex a generation was required.
    """
    entry = (
        f"{_ts()}|reasoning|{provider}|{model}"
        + (f"|tier={tier}" if tier else "")
        + f"|in={input_tokens}|out={output_tokens}"
        + (f"|ctx={context_chars}" if context_chars else "")
        + (f"|qry={query_chars}" if query_chars else "")
        + (f"|rsp={response_chars}" if response_chars else "")
        + f"|cost=${cost_usd:.5f}|elapsed={elapsed_ms}ms|turns={turns}"
        + f"|via={escalation_reason or 'primary'}"
        + f"|resp={response_summary[:120].replace(chr(10), ' ')}"
    )
    _prepend("reasoning_calls.log", entry)


def log_ne_run(
    *,
    obs_count: int = 0,
    integrated: int = 0,
    promoted: int = 0,
    impulses: int = 0,
    model: str = "",
    elapsed_ms: int = 0,
    skipped: bool = False,
    skip_reason: str = "",
) -> None:
    """Log one Narrative Engine run (or skip)."""
    if skipped:
        entry = f"{_ts()}|ne_run|SKIPPED|{skip_reason}"
    else:
        entry = (
            f"{_ts()}|ne_run|OK|model={model}"
            f"|obs={obs_count}|integrated={integrated}"
            f"|promoted={promoted}|impulses={impulses}"
            f"|elapsed={elapsed_ms}ms"
        )
    _prepend("ne_runs.log", entry)


def log_self_edit(
    *,
    file: str,
    change_summary: str = "",
    syntax_ok: bool = True,
    reason: str = "",
    git_hash: str = "",
    blocked: bool = False,
    block_reason: str = "",
) -> None:
    """Log a self-edit attempt (successful, syntax-failed, or blocked)."""
    if blocked:
        entry = f"{_ts()}|self_edit|BLOCKED|file={file}|{block_reason}"
    elif not syntax_ok:
        entry = f"{_ts()}|self_edit|SYNTAX_FAIL|file={file}|reason={reason[:80]}"
    else:
        entry = (
            f"{_ts()}|self_edit|OK"
            f"|file={file}|git={git_hash or 'uncommitted'}"
            f"|reason={reason[:80]}"
            f"|summary={change_summary[:80]}"
        )
    _prepend("self_edit.log", entry)


def log_tool_call(
    *,
    tool_name: str,
    args_summary: str = "",
    result_summary: str = "",
    success: bool = True,
    elapsed_ms: int = 0,
) -> None:
    """Log one tool invocation."""
    entry = (
        f"{_ts()}|tool|{'OK' if success else 'FAIL'}"
        f"|{tool_name}"
        f"|args={args_summary[:80].replace(chr(10), ' ')}"
        f"|result={result_summary[:80].replace(chr(10), ' ')}"
        f"|elapsed={elapsed_ms}ms"
    )
    _prepend("tool_calls.log", entry)


def log_memory_op(
    *,
    operation: str,  # "store" | "search" | "retrieve" | "update"
    memory_type: str = "",
    narrative_snippet: str = "",
    inertia: float = 0.0,
    why: str = "",
) -> None:
    """Log a memory operation."""
    entry = (
        f"{_ts()}|memory|{operation}|{memory_type}"
        f"|inertia={inertia:.2f}"
        f"|narrative={narrative_snippet[:80].replace(chr(10), ' ')}"
        f"|why={why[:60]}"
    )
    _prepend("memory_ops.log", entry)


def log_routing_decision(
    *,
    est_latency_s: float | None,
    actual_latency_s: float | None = None,
    budget_s: float,
    tier_selected: str,
    cost_score: float = 0.0,
    speed_score: float = 0.0,
    tier_score: float = 0.0,
    escalated: bool = False,
    weights: str = "",
    proc_id: str = "",
) -> None:
    """Log one routing decision: estimated vs actual latency, scores, outcome.

    proc_id (Change 7 / D031): the PROC_ROUTING_* memory that governed this decision.
    Enables future compilation of routing patterns into updated PROCEDURAL memories.
    """
    entry = (
        f"{_ts()}|routing"
        f"|tier={tier_selected}"
        f"|est={f'{est_latency_s:.1f}s' if est_latency_s is not None else 'none'}"
        f"|actual={f'{actual_latency_s:.1f}s' if actual_latency_s is not None else 'none'}"
        f"|budget={budget_s}s"
        f"|cost_score={cost_score:.2f}|speed_score={speed_score:.2f}|tier_score={tier_score:.2f}"
        f"|escalated={escalated}"
        + (f"|{weights}" if weights else "")
        + (f"|proc_id={proc_id}" if proc_id else "")
    )
    _prepend("reasoning_calls.log", entry)


def log_escalation(
    *,
    tier: str,  # final tier chosen: "tier.3" | "tier.3.5" | "tier.4" | ...
    reason: str,  # routing_reason string built in _process_inner
    intent: str = "",  # thalamus intent
    complexity: str = "",  # thalamus complexity: low | medium | high
    preparse_tier: str = "",  # complexity["tier_minimum"] from preparse (before bumps)
    complexity_score: float = 0.0,
    complexity_signals: str = "",
    input_snippet: str = "",  # first 120 chars of user input
    habit_fired: bool = False,  # True if a habit handled this turn (no escalation needed)
) -> None:
    """
    Log one per-turn routing/escalation decision to escalation.log.

    G37 weaning data: each entry records why a tier was chosen and what the input
    looked like. Over many sessions this reveals which escalation reasons are load-
    bearing vs. habitual, guiding the incremental reduction of cloud inference dependence.
    /routing command reads this log.
    """
    entry = (
        f"{_ts()}|escalation"
        f"|tier={tier}"
        f"|reason={reason}"
        f"|intent={intent}"
        f"|complexity={complexity}"
        f"|preparse_base={preparse_tier or 'n/a'}"
        f"|cx_score={complexity_score:.2f}"
        f"|cx_signals={complexity_signals[:80] or 'none'}"
        f"|habit={'yes' if habit_fired else 'no'}"
        f"|input={input_snippet[:120].replace(chr(10), ' ')}"
    )
    _prepend("escalation.log", entry)


_METRIC_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "about",
        "that",
        "this",
        "not",
        "just",
        "so",
    }
)


def compute_boot_orientation_score(first_response: str, ring_tail: list[dict]) -> float:
    """
    Score how well Igor's first response reflects the session context loaded at boot.
    Returns 0.0–1.0: fraction of distinct key terms from ring_tail found in response.
    Pure keyword overlap — no LLM, no cost (#112 phase 1).
    """
    import re

    if not ring_tail or not first_response:
        return 0.0

    def _terms(text: str) -> set:
        words = re.findall(r"\b[a-z]{3,}\b", text.lower())
        return {w for w in words if w not in _METRIC_STOP}

    context_terms: set = set()
    for entry in ring_tail:
        context_terms |= _terms(entry.get("content", ""))

    if not context_terms:
        return 0.0

    response_terms = _terms(first_response)
    matched = context_terms & response_terms
    return len(matched) / len(context_terms)


def log_cognition_metric(
    *,
    metric: str,  # e.g. "boot_orientation", "escalation_rate", "ne_grounding"
    value: float,
    detail: str = "",
) -> None:
    """
    Append one cognitive metric sample to cognition_metrics.log (#112).
    Format: timestamp|metric|value|detail
    Append-only (newest-last) — suitable for trend analysis over time.
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / "cognition_metrics.log"
        entry = f"{_ts()}|{metric}|{value:.4f}|{detail[:200].replace(chr(10), ' ')}\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as _bare_e:
        sys.stderr.write(
            f"[forensic_logger] bare except in wild_igor/igor/cognition/forensic_logger.py: {_bare_e}\n"
        )


def log_anomaly(
    *,
    kind: str,  # RATE_LIMIT | CONTEXT_OVERFLOW | TIER6 | NE_FAIL | ARBITER_BUILDUP
    detail: str = "",
) -> None:
    """
    Write a curated anomaly entry to cc_alerts.log (#105).
    CC reads this at session start and after restarts to surface problems proactively.
    Format: timestamp|kind|detail
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / "cc_alerts.log"
        entry = f"{_ts()}|{kind}|{detail[:200].replace(chr(10), ' ')}\n"
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as _bare_e:
        sys.stderr.write(
            f"[forensic_logger] bare except in wild_igor/igor/cognition/forensic_logger.py: {_bare_e}\n"
        )


def log_batch_call(
    *,
    source: str,  # "local" | "openrouter"
    model: str,
    elapsed_s: float,
    via: str = "",  # host URL or bypass reason
) -> None:
    """Log one batch pool call to metrics.log for performance tracking."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / "cognition_metrics.log"
        entry = (
            f"{_ts()}|batch_call|{source}|model={model}"
            f"|elapsed={elapsed_s:.1f}s" + (f"|via={via}" if via else "") + "\n"
        )
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)
    except Exception as _bare_e:
        sys.stderr.write(
            f"[forensic_logger] bare except in wild_igor/igor/cognition/forensic_logger.py: {_bare_e}\n"
        )


def log_error(
    *,
    kind: str,  # IMPULSE_SKIP | TIER_FAIL | TOOL_FAIL | etc.
    detail: str = "",
    source: str = "",  # e.g. "tier.3" | "impulse/tier.2"
) -> None:
    """
    Log a runtime error or degraded-mode event to errors.log.
    Newest-first. Readable by both Igor (get_error_log tool) and Claude Code (read file).
    """
    entry = (
        f"{_ts()}|ERROR|{kind}"
        + (f"|source={source}" if source else "")
        + (f"|{detail[:200].replace(chr(10), ' ')}" if detail else "")
    )
    _prepend("errors.log", entry)


def log_tier_selection(
    *,
    tiers_available: list,
    preparse_escalate: bool,
    preparse_via: str,  # "ollama" | "openrouter" | "skipped"
    tier_selected: str,  # "tier.1" | "tier.2" | "tier.3" | ...
    reason: str,
    complexity_score: float = 0.0,
    complexity_signals: str = "",
) -> None:
    """Log which tier was selected before each inference call (local or cloud)."""
    entry = (
        f"{_ts()}|tier_select"
        f"|available={','.join(tiers_available)}"
        f"|preparse_via={preparse_via}"
        f"|escalate={preparse_escalate}"
        f"|selected={tier_selected}"
        f"|reason={reason}"
        f"|complexity={complexity_score:.2f}"
        f"|signals=[{complexity_signals}]"
    )
    _prepend("reasoning_calls.log", entry)


def log_reading_progress(
    *,
    passage: str = "",
    word_count: int = 0,
    book_title: str = "",
    thread_id: str = "",
) -> None:
    """
    Log a reading-session turn to reading_progress.log.

    Called whenever Igor responds to a creative_request (read-aloud / narration).
    The log is newest-first like all forensic logs — easy to tail and review
    how far Igor got through a text.
    """
    entry = (
        f"{_ts()}|reading"
        + (f"|book={book_title[:60].replace('|', '_')}" if book_title else "")
        + (f"|thread={thread_id}" if thread_id else "")
        + f"|words={word_count}"
        + f"|passage={passage[:300].replace(chr(10), ' ')}"
    )
    _prepend("reading_progress.log", entry)


# ── Pipeline trace (per-step timing, 24-hour rotation) ────────────────────────


def log_pipeline_step(
    *,
    turn_id: str,
    step: str,
    elapsed_ms: int,
    **kwargs,
) -> None:
    """
    Append one pipeline step timing entry to pipeline_trace.YYYYMMDD.log.

    24-hour rotation: one file per calendar day (pipeline_trace.20260312.log).
    Files older than 1 day are purged on each write — no manual cleanup needed.
    Append-only (newest last) — suitable for grep and time-series streaming.

    Called from _process_inner() for each named pipeline step, and from
    _winnow_context() via get_turn_id() for the winnow step.

    Steps: preamble | thalamus | bg_prospect | preparse_search | routing |
           habit_exec | think_build | think_llm | winnow | reasoning |
           mem_store | TOTAL
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        path = LOG_DIR / f"pipeline_trace.{today}.log"
        parts = [_ts(), "PT", f"turn={turn_id}", f"step={step}", f"ms={elapsed_ms}"]
        for k, v in kwargs.items():
            sv = str(v)[:80].replace("|", "_").replace("\n", " ")
            parts.append(f"{k}={sv}")
        with path.open("a", encoding="utf-8") as f:
            f.write("|".join(parts) + "\n")
        _purge_old_pipeline_traces(today)
        # Also feed into TurnContext (#203) — dual use, no extra call sites
        turn_ctx_update(
            step, {"ms": elapsed_ms, **{k: str(v)[:80] for k, v in kwargs.items()}}
        )
    except Exception as _bare_e:
        sys.stderr.write(
            f"[forensic_logger] bare except in wild_igor/igor/cognition/forensic_logger.py: {_bare_e}\n"
        )


def _purge_old_pipeline_traces(today: str) -> None:
    """Delete pipeline_trace logs from before yesterday."""
    try:
        from datetime import datetime as _dt, timedelta

        cutoff = (_dt.strptime(today, "%Y%m%d") - timedelta(days=1)).strftime("%Y%m%d")
        for p in LOG_DIR.glob("pipeline_trace.*.log"):
            date_part = p.stem.split(".")[-1]
            if date_part.isdigit() and date_part < cutoff:
                p.unlink(missing_ok=True)
    except Exception as _bare_e:
        sys.stderr.write(
            f"[forensic_logger] bare except in wild_igor/igor/cognition/forensic_logger.py: {_bare_e}\n"
        )


# ── Inference I/O log ─────────────────────────────────────────────────────────
# Full prompt+response for every model call. Daily rotating, append-only
# (not prepend — entries can be large). Purge after 2 days.
# File: ~/.TheIgors/logs/inference_io.YYYYMMDD.log
# Gate: IGOR_LOG_INFERENCE_IO (default true — disable to save disk)

_INFERENCE_IO_PROMPT_CAP = 16 * 1024  # 16 KB max per prompt
_INFERENCE_IO_RESP_CAP = 8 * 1024  # 8 KB max per response


def log_inference_io(
    *,
    provider: str,  # "ollama" | "openrouter" | "anthropic"
    model: str,
    tier: str = "",
    turn_id: str = "",
    prompt: str,  # full prompt sent to model (system + user + context)
    response: str,  # full text response received
    elapsed_ms: int = 0,
    call_type: str = "reason",  # "reason" | "preparse" | "winnow" | "ne" | "think"
) -> None:
    """
    Log full prompt + response for every model inference call.

    Append-only, daily rotation (inference_io.YYYYMMDD.log), purged after 2 days.
    Prompt capped at 16KB, response at 8KB so the log stays manageable.
    Gate: IGOR_LOG_INFERENCE_IO (default true).
    """
    try:
        import os as _os

        if _os.getenv("IGOR_LOG_INFERENCE_IO", "true").lower() in ("0", "false", "no"):
            return

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        path = LOG_DIR / f"inference_io.{today}.log"

        tid = turn_id or get_turn_id()
        hdr = (
            f"=== {_ts()} | {provider}/{model}"
            + (f" | {tier}" if tier else "")
            + (f" | turn={tid}" if tid and tid != "?" else "")
            + (f" | {call_type}" if call_type else "")
            + f" | {elapsed_ms}ms ==="
        )
        prompt_block = prompt[:_INFERENCE_IO_PROMPT_CAP]
        response_block = response[:_INFERENCE_IO_RESP_CAP]
        if len(prompt) > _INFERENCE_IO_PROMPT_CAP:
            prompt_block += (
                f"\n...[truncated {len(prompt) - _INFERENCE_IO_PROMPT_CAP} chars]"
            )
        if len(response) > _INFERENCE_IO_RESP_CAP:
            response_block += (
                f"\n...[truncated {len(response) - _INFERENCE_IO_RESP_CAP} chars]"
            )

        entry = (
            f"\n{hdr}\n"
            f"--- PROMPT ---\n{prompt_block}\n"
            f"--- RESPONSE ---\n{response_block}\n"
            f"--- END ---\n"
        )

        with path.open("a", encoding="utf-8") as f:
            f.write(entry)

        _purge_old_inference_io(today)

    except Exception as _bare_e:
        sys.stderr.write(
            f"[forensic_logger] bare except in wild_igor/igor/cognition/forensic_logger.py: {_bare_e}\n"
        )


def _purge_old_inference_io(today: str) -> None:
    """Delete inference_io logs older than 2 days."""
    try:
        from datetime import datetime as _dt, timedelta

        cutoff = (_dt.strptime(today, "%Y%m%d") - timedelta(days=2)).strftime("%Y%m%d")
        for p in LOG_DIR.glob("inference_io.*.log"):
            date_part = p.stem.split(".")[-1]
            if date_part.isdigit() and date_part < cutoff:
                p.unlink(missing_ok=True)
    except Exception as _bare_e:
        sys.stderr.write(
            f"[forensic_logger] bare except in wild_igor/igor/cognition/forensic_logger.py: {_bare_e}\n"
        )


# ── Interaction log (#201) ────────────────────────────────────────────────────
# Tier-1 triage: one line per turn. Smallest useful log — scan first.
# Format: timestamp|turn_id|thread_id|tier|elapsed_ms|cost_usd|IN:...|OUT:...


def log_interaction(
    *,
    turn_id: str = "",
    thread_id: str = "",
    tier: str = "",
    elapsed_ms: int = 0,
    cost_usd: float = 0.0,
    input_text: str = "",
    response_text: str = "",
) -> None:
    """
    Append one line to interaction.YYYYMMDD.log.

    This is the first log to read when something goes wrong.
    `turn_id` is the join key to pipeline_trace, inference_io, and turn_trace.
    Daily rotation, keep 7 days, append-only (newest last).
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y%m%d")
        path = LOG_DIR / f"interaction.{today}.log"

        tid = turn_id or get_turn_id()
        in_preview = input_text[:120].replace("|", "_").replace("\n", " ")
        out_preview = response_text[:120].replace("|", "_").replace("\n", " ")

        entry = (
            f"{_ts()}|{tid}|{thread_id or '?'}|{tier or '?'}"
            f"|{elapsed_ms}ms|${cost_usd:.5f}"
            f"|IN:{in_preview}|OUT:{out_preview}\n"
        )
        with path.open("a", encoding="utf-8") as f:
            f.write(entry)

        _purge_old_interaction(today)
    except Exception as _bare_e:
        sys.stderr.write(
            f"[forensic_logger] bare except in wild_igor/igor/cognition/forensic_logger.py: {_bare_e}\n"
        )


def _purge_old_interaction(today: str) -> None:
    """Delete interaction logs older than 7 days."""
    try:
        from datetime import datetime as _dt, timedelta

        cutoff = (_dt.strptime(today, "%Y%m%d") - timedelta(days=7)).strftime("%Y%m%d")
        for p in LOG_DIR.glob("interaction.*.log"):
            date_part = p.stem.split(".")[-1]
            if date_part.isdigit() and date_part < cutoff:
                p.unlink(missing_ok=True)
    except Exception as _bare_e:
        sys.stderr.write(
            f"[forensic_logger] bare except in wild_igor/igor/cognition/forensic_logger.py: {_bare_e}\n"
        )


# ── Startup log (#202) ────────────────────────────────────────────────────────
# Tier-1 triage: one block per boot. Know immediately if a boot was healthy.
# Keep last 50 boots (trim on write). Append-only, newest last.

_STARTUP_LOG_MAX_BOOTS = 50


def log_startup(
    *,
    instance_id: str = "",
    memory_count: int = 0,
    habit_count: int = 0,
    wg_words: int = 0,
    boot_elapsed_s: float = 0.0,
    embed_ok: bool = True,
    integrity_ok: bool = True,
    warm_context: str = "none",  # "loaded" | "none" | "gap=Xh"
    ollama_status: str = "",  # "healthy(model)" | "unavailable"
    openrouter_status: str = "",  # "healthy($X.XX)" | "no_key"
    cloud_mode: str = "off",
    notes: str = "",
) -> None:
    """
    Append one boot block to startup.log.

    Called from Igor.run() after _boot_ready = True.
    Keeps last 50 boot blocks — trims oldest when over limit.
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / "startup.log"

        block = (
            f"=== BOOT {_ts()} | {instance_id} | {boot_elapsed_s:.1f}s ===\n"
            f"memories={memory_count} habits={habit_count} wg_words={wg_words}\n"
            f"embed={'ok' if embed_ok else 'FAIL'} "
            f"integrity={'ok' if integrity_ok else 'FAIL'} "
            f"warm_context={warm_context}\n"
            f"ollama={ollama_status or 'unknown'} "
            f"openrouter={openrouter_status or 'unknown'} "
            f"cloud_mode={cloud_mode}\n"
            + (f"notes={notes}\n" if notes else "")
            + "=== BOOT READY ===\n\n"
        )

        # Append new block
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        combined = existing + block

        # Trim to last _STARTUP_LOG_MAX_BOOTS boot blocks
        blocks = combined.split("=== BOOT READY ===\n\n")
        if len(blocks) > _STARTUP_LOG_MAX_BOOTS + 1:
            blocks = blocks[-((_STARTUP_LOG_MAX_BOOTS + 1)) :]
        path.write_text("=== BOOT READY ===\n\n".join(blocks), encoding="utf-8")
    except Exception as _bare_e:
        sys.stderr.write(
            f"[forensic_logger] bare except in wild_igor/igor/cognition/forensic_logger.py: {_bare_e}\n"
        )


# ── /trace command helper (#203) ──────────────────────────────────────────────


def read_last_turn_traces(n: int = 5) -> str:
    """
    Return the last N turn traces from today's turn_trace log as a string.
    Used by the /trace command in _handle_command().
    """
    try:
        today = datetime.now().strftime("%Y%m%d")
        path = LOG_DIR / f"turn_trace.{today}.log"
        if not path.exists():
            # Fall back to yesterday
            from datetime import timedelta

            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
            path = LOG_DIR / f"turn_trace.{yesterday}.log"
        if not path.exists():
            return "(no turn_trace log found)"

        text = path.read_text(encoding="utf-8")
        # Split on "=== END ===" boundary
        blocks = [b.strip() for b in text.split("=== END ===") if b.strip()]
        selected = blocks[-n:] if len(blocks) >= n else blocks
        return "\n\n=== END ===\n\n".join(selected) + "\n\n=== END ==="
    except Exception as e:
        return f"(error reading turn_trace: {e})"
