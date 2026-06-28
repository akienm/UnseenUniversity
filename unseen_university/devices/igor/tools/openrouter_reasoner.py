"""
OpenRouter model discovery tools (change.39.addition).

Tools registered:
    list_upstream_models   — query /api/v1/models, return cost+context table
    compare_upstream_costs — suggest cheapest model adequate for a described task

Env vars:
    OPENROUTER_API_KEY  — required (same key used by the reasoner)
"""

import json
import os
import urllib.request
import urllib.error

from unseen_university.devices.igor.tools.registry import Tool, registry

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def _fetch_models() -> list[dict]:
    """Fetch model list from OpenRouter. Returns raw list of model dicts."""
    token = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not token:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    req = urllib.request.Request(
        f"{OPENROUTER_BASE}/models",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()).get("data", [])
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OpenRouter /models error {e.code}: {e.read().decode()[:200]}")


def _format_cost(per_token: float | None) -> str:
    """Convert per-token cost to human-readable per-1k cost."""
    if per_token is None:
        return "?"
    per_1k = float(per_token) * 1000
    if per_1k == 0:
        return "free"
    if per_1k < 0.001:
        return f"${per_1k:.5f}"
    return f"${per_1k:.4f}"


def list_cloud_models(filter: str = "") -> str:
    """
    List models available via OpenRouter with context length and cost.
    filter: optional substring to match against model id (e.g. 'mistral', 'free', 'claude').
    Returns a table: model_id | context | $/1k_in | $/1k_out
    """
    try:
        models = _fetch_models()
    except Exception as e:
        return f"Error fetching models: {e}"

    filt = filter.strip().lower()
    if filt:
        models = [m for m in models if filt in m.get("id", "").lower()]

    if not models:
        return f"No models found{' matching ' + repr(filter) if filt else ''}."

    # Sort by input cost ascending (free/unknown last)
    def sort_key(m):
        pricing = m.get("pricing", {})
        try:
            return float(pricing.get("prompt", 0) or 0)
        except (TypeError, ValueError):
            return 1e9

    models.sort(key=sort_key)

    header = f"{'Model':<45} {'Context':>8}  {'$/1k in':>9}  {'$/1k out':>10}"
    sep = "-" * len(header)
    lines = [header, sep]
    for m in models[:40]:  # cap at 40 rows
        mid = m.get("id", "?")[:44]
        ctx = m.get("context_length") or m.get("top_provider", {}).get("context_length")
        ctx_str = f"{ctx // 1000}k" if ctx else "?"
        pricing = m.get("pricing", {})
        inp = _format_cost(pricing.get("prompt"))
        out = _format_cost(pricing.get("completion"))
        lines.append(f"{mid:<45} {ctx_str:>8}  {inp:>9}  {out:>10}")

    if len(models) > 40:
        lines.append(f"... and {len(models) - 40} more (use a filter to narrow)")
    return "\n".join(lines)


def compare_cloud_costs(task_description: str) -> str:
    """
    Given a task description, suggest the cheapest model likely adequate for it.
    Returns a ranked shortlist with cost and rationale.
    """
    try:
        models = _fetch_models()
    except Exception as e:
        return f"Error fetching models: {e}"

    desc = task_description.lower()

    # Simple heuristic: classify task complexity
    high_complexity_signals = ("code", "debug", "reasoning", "math", "complex", "analysis", "architecture")
    low_complexity_signals = ("classify", "simple", "tag", "label", "summarize", "short", "quick", "yes/no")

    if any(s in desc for s in high_complexity_signals):
        complexity = "high"
        min_ctx = 8000
    elif any(s in desc for s in low_complexity_signals):
        complexity = "low"
        min_ctx = 0
    else:
        complexity = "medium"
        min_ctx = 4000

    # Filter: must have pricing info; apply min context
    candidates = []
    for m in models:
        pricing = m.get("pricing", {})
        try:
            inp_cost = float(pricing.get("prompt", 0) or 0)
        except (TypeError, ValueError):
            continue
        ctx = m.get("context_length") or m.get("top_provider", {}).get("context_length") or 0
        if ctx < min_ctx:
            continue
        candidates.append((inp_cost, m))

    candidates.sort(key=lambda x: x[0])

    lines = [
        f"Task: {task_description[:100]}",
        f"Complexity assessed: {complexity}  (min context: {min_ctx // 1000}k tokens)",
        "",
        "Recommended models (cheapest first):",
    ]
    for inp_cost, m in candidates[:8]:
        mid = m.get("id", "?")
        ctx = m.get("context_length") or m.get("top_provider", {}).get("context_length")
        ctx_str = f"{ctx // 1000}k" if ctx else "?"
        pricing = m.get("pricing", {})
        inp = _format_cost(pricing.get("prompt"))
        out = _format_cost(pricing.get("completion"))
        lines.append(f"  {mid:<44}  ctx={ctx_str}  in={inp}  out={out}")

    if not candidates:
        lines.append("  (no suitable models found — check OPENROUTER_API_KEY)")

    lines.append("")
    lines.append("To use: /cloud add MODEL_ID  or  /relay start MODEL_ID")
    return "\n".join(lines)


# ── Register tools ─────────────────────────────────────────────────────────────

registry.register(Tool(
    name="list_cloud_models",
    description=(
        "List models available via OpenRouter (cloud inference) with context length and pricing. "
        "Use filter to narrow by provider or capability, e.g. 'mistral', 'claude', 'free'. "
        "Returns a table: model_id | context | cost_per_1k_input | cost_per_1k_output."
    ),
    parameters={
        "type": "object",
        "properties": {
            "filter": {
                "type": "string",
                "description": "Optional substring to match against model ID (e.g. 'mistral', 'free', 'claude'). Leave blank for all.",
            },
        },
        "required": [],
    },
    fn=list_cloud_models,
))

registry.register(Tool(
    name="compare_cloud_costs",
    description=(
        "Given a task description, suggest the cheapest OpenRouter cloud inference model adequate for it. "
        "Classifies task complexity (low/medium/high) and filters by required context length. "
        "Returns a ranked shortlist with costs and instructions to activate the model."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_description": {
                "type": "string",
                "description": "Plain-English description of the task (e.g. 'simple yes/no classification', 'complex code reasoning')",
            },
        },
        "required": ["task_description"],
    },
    fn=compare_cloud_costs,
))
