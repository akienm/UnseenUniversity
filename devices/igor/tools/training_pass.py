"""
training_pass.py — Automated self-training via inner Claude (D270).

Reads recent cloud escalations from reasoning_calls.log, passes them to
call_inner_cc_long() (Haiku with prompt caching), and deposits the resulting
INTERPRETIVE/NARRATIVE_GAP memories into cortex.

The loop: cloud escape → training pass identifies gap → INTERPRETIVE node
deposited → next time the pattern is local. Each pass makes the next pass
cheaper.

Usage (via habit or cc_send):
  "start training pass"
  → PROC_TRAINING_PASS fires → start_training_pass() called
  → Reads last N cloud escalations from reasoning_calls.log
  → Calls inner_cc_long (Haiku, prompt-cached) with escalation context
  → Deposits gap memories to cortex
  → Returns report: N escapes analyzed, N nodes deposited

Config (optional JSON):
  {"n_traces": 10, "min_tier": "tier.3.5", "model": "anthropic/claude-haiku-4-5-20251001"}
"""

import json
import os
import pathlib
import re

from .registry import Tool, registry

_LOGS_DIR = pathlib.Path(os.path.expanduser("~/.TheIgors/logs"))
_REASONING_LOG = _LOGS_DIR / "reasoning_calls.log"

# Tiers considered "cloud escalations" worth analyzing
_CLOUD_TIERS = {"tier.3", "tier.3.5", "tier.4"}

_TRAINING_TASK_TEMPLATE = """\
TRAINING ANALYSIS TASK

You are analyzing cloud inference escalations for Igor, an AI agent. Each \
escalation below is a turn where Igor had to call a cloud model instead of \
handling it locally. Your job: identify patterns and formulate INTERPRETIVE \
memories that would help Igor recognize and handle similar situations without \
escalating to cloud.

CLOUD ESCALATIONS (most recent first):
{escalations}

For each pattern you identify, return a JSON node in the "nodes" array:
  - type: "interpretive" for insight about when/why cloud was needed
  - type: "factual" for stable facts that would enable local handling
  - narrative: 1-2 sentences, generalizable (not session-specific)
  - confidence: 0.7+ only

Focus on: what triggered the escalation? What knowledge, if present locally, \
would have allowed tier.2 (Ollama) to handle it? What is the underlying gap?

Respond with the standard inner_cc JSON format:
{{
  "answer": "brief summary of patterns found",
  "nodes": [...],
  "follow_up": ""
}}
"""


def _read_cloud_escalations(n: int, min_tier: str = "tier.3.5") -> list[dict]:
    """
    Read last N cloud escalation lines from reasoning_calls.log.
    Returns list of dicts: {ts, tier, model, cost, response}.
    """
    cloud_tiers = {t for t in _CLOUD_TIERS if t >= min_tier}
    if not _REASONING_LOG.exists():
        return []

    escalations: list[dict] = []
    try:
        lines = _REASONING_LOG.read_text(errors="ignore").splitlines()
        for line in reversed(lines):
            if "|reasoning|" not in line:
                continue
            # Format: ts|reasoning|provider|model|tier=X|in=N|...|resp=...
            parts = line.split("|", maxsplit=6)
            if len(parts) < 7:
                continue
            tier_field = next((p for p in parts if p.startswith("tier=")), "")
            tier = tier_field.replace("tier=", "")
            if tier not in cloud_tiers:
                continue
            model_field = parts[3] if len(parts) > 3 else ""
            # Extract resp= from the tail
            resp = ""
            resp_match = re.search(r"\|resp=(.+)$", line)
            if resp_match:
                resp = resp_match.group(1)[:300]
            escalations.append(
                {
                    "ts": parts[0],
                    "tier": tier,
                    "model": model_field,
                    "response": resp,
                }
            )
            if len(escalations) >= n:
                break
    except Exception as _re:
        _log_error(kind="TRAINING_PASS_FAIL", detail=f"read_escalations: {_re}")

    return escalations


def _get_cortex():
    from ..memory.cortex import Cortex as _Cortex

    return _Cortex(None)


def start_training_pass(config: str = "") -> str:
    """
    Run a self-training pass: read cloud escalations → inner Claude gap
    analysis → deposit INTERPRETIVE memories.

    config: optional JSON string with overrides:
      {"n_traces": 10, "min_tier": "tier.3.5",
       "model": "anthropic/claude-haiku-4-5-20251001", "max_turns": 8}

    Returns a report: escalations analyzed, nodes deposited, summary.
    """
    params: dict = {}
    if config and config.strip():
        try:
            params = json.loads(config)
        except (json.JSONDecodeError, ValueError):
            pass

    n_traces = int(params.get("n_traces", 10))
    min_tier = str(params.get("min_tier", "tier.3.5"))
    from .inner_cc import _HAIKU_MODEL

    model = str(params.get("model", _HAIKU_MODEL))
    max_turns = int(params.get("max_turns", 8))

    # ── 1. Read cloud escalations ─────────────────────────────────────────
    escalations = _read_cloud_escalations(n_traces, min_tier)
    if not escalations:
        return (
            "Training pass: no cloud escalations found in reasoning_calls.log "
            f"(min_tier={min_tier}). Nothing to analyze."
        )

    # ── 2. Build task for inner Claude ────────────────────────────────────
    esc_block = "\n\n".join(
        f"[{e['ts']}] tier={e['tier']} model={e['model']}\n"
        f"Response: {e['response']}"
        for e in escalations
    )
    task = _TRAINING_TASK_TEMPLATE.format(escalations=esc_block)

    # ── 3. Call inner_cc_long ─────────────────────────────────────────────
    cortex = _get_cortex()
    deposited = 0
    summary = "(no analysis)"
    try:
        from .inner_cc import call_inner_cc_long

        result = call_inner_cc_long(
            task=task,
            model=model,
            cortex=cortex,
            max_turns=max_turns,
        )
        summary = result.get("answer", "(no answer)")
        deposited = len(result.get("nodes", []))
    except Exception as _ce:
        _log_error(kind="TRAINING_PASS_FAIL", detail=f"inner_cc_long: {_ce}")
        return f"[training_pass ERROR] inner_cc_long failed: {_ce}"

    return (
        f"Training pass complete — {len(escalations)} escalation(s) analyzed\n"
        f"Nodes deposited: {deposited}\n"
        f"Model: {model}\n"
        f"Summary: {summary}"
    )


def _log_error(kind: str, detail: str) -> None:
    try:
        from ..cognition.forensic_logger import log_error as _le

        _le(kind=kind, detail=detail)
    except Exception:
        import logging

        logging.getLogger(__name__).error("training_pass %s: %s", kind, detail)


# ── Registration ──────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="start_training_pass",
        description=(
            "Run a self-training pass: reads recent cloud inference escalations, "
            "calls inner Claude (Haiku with prompt caching) to identify gap patterns, "
            "deposits INTERPRETIVE memories to cortex. "
            "Each pass reduces future cloud escapes by depositing local knowledge. "
            'Optional config JSON: {"n_traces": 10, "min_tier": "tier.3.5"}.'
        ),
        parameters={
            "type": "object",
            "properties": {
                "config": {
                    "type": "string",
                    "description": (
                        'Optional JSON: {"n_traces": 10, "min_tier": "tier.3.5", '
                        '"model": "anthropic/claude-haiku-4-5-20251001", "max_turns": 8}'
                    ),
                },
            },
            "required": [],
        },
        fn=start_training_pass,
    )
)
