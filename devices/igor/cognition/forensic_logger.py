"""
Forensic Logger — WO3: Cognition Stabilization Phase 3.

CSB-format structured logs at ~/.TheIgors/logs/ for post-mortem analysis.
All logs are newest-first (prepend, not append). Rotate at 10MB.

Log files:
    reasoning_calls.log  — every upstream API call (anthropic, openrouter)
    ne_runs.log          — every Narrative Engine run
    self_edit.log        — every self-edit attempt (allowed or blocked)
    tool_calls.log       — every tool invocation (high-volume; optional)
    memory_ops.log       — memory store/search operations (optional)

All functions are fire-and-forget: exceptions are swallowed so logging
can never crash the main loop.
"""

from datetime import datetime
from pathlib import Path

LOG_DIR   = Path.home() / ".TheIgors" / "logs"
MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


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
    except Exception:
        pass  # Logging must never crash the main loop


# ── Public log functions ──────────────────────────────────────────────────────

def log_reasoning_call(
    *,
    provider: str,           # "anthropic" | "openrouter" | "ollama"
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    elapsed_ms: int = 0,
    turns: int = 1,
    response_summary: str = "",
    escalation_reason: str = "",
) -> None:
    """Log one upstream API call (full tool-loop, not per-turn)."""
    entry = (
        f"{_ts()}|reasoning|{provider}|{model}"
        f"|in={input_tokens}|out={output_tokens}"
        f"|cost=${cost_usd:.5f}|elapsed={elapsed_ms}ms|turns={turns}"
        f"|via={escalation_reason or 'primary'}"
        f"|resp={response_summary[:120].replace(chr(10), ' ')}"
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
    operation: str,          # "store" | "search" | "retrieve" | "update"
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


_METRIC_STOP = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "i", "you", "he", "she", "it", "we",
    "they", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "about", "that", "this", "not", "just", "so",
})


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
        words = re.findall(r'\b[a-z]{3,}\b', text.lower())
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
    metric: str,         # e.g. "boot_orientation", "escalation_rate", "ne_grounding"
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
    except Exception:
        pass


def log_anomaly(
    *,
    kind: str,           # RATE_LIMIT | CONTEXT_OVERFLOW | TIER6 | NE_FAIL | ARBITER_BUILDUP
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
    except Exception:
        pass  # Logging must never crash the main loop


def log_tier_selection(
    *,
    tiers_available: list,
    preparse_escalate: bool,
    preparse_via: str,        # "ollama" | "openrouter" | "skipped"
    tier_selected: str,       # "tier.1" | "tier.2" | "tier.3" | ...
    reason: str,
    complexity_score: float = 0.0,
    complexity_signals: str = "",
) -> None:
    """Log which tier was selected before each upstream call."""
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
