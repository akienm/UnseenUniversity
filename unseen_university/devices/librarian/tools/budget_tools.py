"""Budget tools — OR balance and burn rate, queryable from CC via MCP."""

from __future__ import annotations

import json
import os
import time
import urllib.request
from datetime import datetime, timezone

SCHEMAS = [
    {
        "name": "budget_summary",
        "description": (
            "Return per-agent inference spend from the budget ledger, grouped by session, "
            "CoA, or instance. Shows token counts and cost_usd for OR calls. "
            "Use to answer 'how much has igor spent this session?' or 'show me spend by coa'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent to query (e.g. 'igor', 'cc')",
                },
                "group_by": {
                    "type": "string",
                    "enum": ["session", "coa", "instance"],
                    "description": "Dimension to group spend by (default: session)",
                },
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "budget_limit_set",
        "description": (
            "Set or update a per-agent budget limit. scope='session' enforces a hard "
            "USD cap per session_id for OpenRouter calls; scope='overall' is recorded "
            "but not yet enforced. Returns ok/error."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent to limit"},
                "scope": {
                    "type": "string",
                    "enum": ["session", "overall"],
                    "description": "Limit scope",
                },
                "limit_usd": {
                    "type": "number",
                    "description": "Hard limit in USD",
                },
            },
            "required": ["agent_id", "scope", "limit_usd"],
        },
    },
    {
        "name": "budget_remaining",
        "description": (
            "Return remaining USD budget for an agent's session. "
            "Shows remaining_usd, pct_used, limit_usd, and spent_usd. "
            "remaining_usd is null when no session limit has been set."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent to query"},
                "session_id": {"type": "string", "description": "Session to check"},
            },
            "required": ["agent_id", "session_id"],
        },
    },
    {
        "name": "check_openrouter_balance",
        "description": (
            "Fetch the current OpenRouter account balance from the OR API (cached 1h). "
            "Returns balance, total purchased, total used, and burn trajectory. "
            "Use this to answer 'how much OR budget is left?' or 'how fast are we spending?'. "
            "Does NOT charge any credits to call."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "openrouter_burn_rate",
        "description": (
            "Show OR balance burn rate and days remaining based on stored balance history. "
            "Reads infra.balance_history snapshots (written hourly by the inference device "
            "and Igor). window_hours controls how far back to look (default 48)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "window_hours": {
                    "type": "number",
                    "description": "Lookback window in hours (default 48)",
                },
            },
            "required": [],
        },
    },
]

_OR_CREDITS_URL = "https://openrouter.ai/api/v1/credits"
_CACHE_TTL = 3600.0
_cache: dict = {}


def _api_key() -> str:
    return (
        os.environ.get("OPENROUTER_MANAGEMENT_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
        or ""
    )


def _db_url() -> str:
    return os.environ.get("UU_HOME_DB_URL", "")


def _fetch_or_balance() -> dict | None:
    global _cache
    now = time.time()
    if _cache and (now - _cache.get("fetched_at", 0)) < _CACHE_TTL:
        return _cache.copy()
    api_key = _api_key()
    if not api_key:
        return None
    try:
        req = urllib.request.Request(
            _OR_CREDITS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())["data"]
        result = {
            "purchased": float(data["total_credits"]),
            "used": float(data["total_usage"]),
            "balance": float(data["total_credits"]) - float(data["total_usage"]),
            "fetched_at": now,
        }
        _cache = result
        return result.copy()
    except Exception as exc:
        return None


def _burn_trajectory(window_hours: float = 48.0) -> dict:
    db_url = _db_url()
    if not db_url:
        return {"trend": "no_data", "note": "UU_HOME_DB_URL not set"}
    try:
        import psycopg2
        import psycopg2.extras

        cutoff = datetime.fromtimestamp(
            time.time() - window_hours * 3600, tz=timezone.utc
        ).isoformat()
        with psycopg2.connect(db_url) as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT timestamp, balance FROM infra.balance_history"
                    " WHERE timestamp >= %s ORDER BY timestamp ASC",
                    (cutoff,),
                )
                rows = cur.fetchall()
    except Exception as exc:
        return {"trend": "no_data", "note": f"DB error: {exc}"}

    if len(rows) < 2:
        bal = float(rows[0]["balance"]) if rows else 0.0
        return {
            "trend": "no_data",
            "balance_now": bal,
            "sample_count": len(rows),
            "note": "insufficient history",
        }

    def _ts(v):
        return (
            v.timestamp()
            if isinstance(v, datetime)
            else datetime.fromisoformat(v).timestamp()
        )

    t0, t1 = _ts(rows[0]["timestamp"]), _ts(rows[-1]["timestamp"])
    b0, b1 = float(rows[0]["balance"]), float(rows[-1]["balance"])
    elapsed_days = (t1 - t0) / 86400.0
    if elapsed_days < 1e-6:
        return {"trend": "no_data", "balance_now": b1, "sample_count": len(rows)}

    burn_per_day = (b0 - b1) / elapsed_days
    days_remaining = (b1 / burn_per_day) if burn_per_day > 0.001 else float("inf")
    trend = (
        "burning_fast"
        if burn_per_day > 20
        else ("burning" if burn_per_day > 5 else "stable")
    )

    return {
        "trend": trend,
        "burn_per_day": round(burn_per_day, 2),
        "days_remaining": (
            round(days_remaining, 1) if days_remaining != float("inf") else None
        ),
        "balance_now": round(b1, 2),
        "sample_count": len(rows),
        "window_hours": round(window_hours),
    }


def _check_openrouter_balance() -> str:
    result = _fetch_or_balance()
    if result is None:
        return "OR balance unavailable — OPENROUTER_API_KEY not set or API unreachable."
    age_min = (time.time() - result["fetched_at"]) / 60
    traj = _burn_trajectory()
    lines = [
        f"OpenRouter balance (fetched {age_min:.0f}m ago):",
        f"  Purchased: ${result['purchased']:.2f}",
        f"  Used:      ${result['used']:.4f}",
        f"  Remaining: ${result['balance']:.4f}",
    ]
    if traj.get("trend") not in (None, "no_data"):
        dr = traj["days_remaining"]
        dr_str = f"{dr:.1f}d" if dr is not None else "∞"
        lines.append(
            f"  Burn rate: ${traj['burn_per_day']:.2f}/day ({traj['trend']}) — "
            f"~{dr_str} remaining  [{traj['sample_count']} samples, {traj['window_hours']}h window]"
        )
    else:
        note = traj.get("note", "")
        lines.append(f"  Burn rate: {note or 'insufficient history'}")
    return "\n".join(lines)


def _openrouter_burn_rate(window_hours: float = 48.0) -> str:
    traj = _burn_trajectory(window_hours)
    if traj.get("trend") == "no_data":
        note = traj.get("note", "")
        cnt = traj.get("sample_count", 0)
        return (
            f"Insufficient OR balance history ({cnt} sample(s) in {window_hours:.0f}h window"
            + (f" — {note}" if note else "")
            + "). History populates hourly as inference calls are made."
        )
    dr = traj["days_remaining"]
    dr_str = f"{dr:.1f} days" if dr is not None else "unlimited (no meaningful burn)"
    return (
        f"OR Burn Rate ({traj['window_hours']}h window, {traj['sample_count']} samples):\n"
        f"  Current balance: ${traj['balance_now']:.2f}\n"
        f"  Burn rate:       ${traj['burn_per_day']:.2f}/day  ({traj['trend']})\n"
        f"  Days remaining:  {dr_str}"
    )


def _budget_summary(agent_id: str, group_by: str = "session") -> str:
    try:
        from devices.inference.budget_ledger import budget_summary

        rows = budget_summary(agent_id, group_by=group_by)
    except Exception as exc:
        return f"budget_summary error: {exc}"
    if not rows:
        return f"No ledger entries for agent_id={agent_id!r}."
    lines = [f"Spend for {agent_id!r} grouped by {group_by}:"]
    for r in rows:
        cost = f"  cost=${r['cost_usd_total']:.4f}" if r["cost_usd_total"] else ""
        lines.append(
            f"  {r['group_key'] or '(none)'}: "
            f"in={r['input_tokens']} out={r['output_tokens']}"
            f"{cost}  calls={r['call_count']}"
        )
    return "\n".join(lines)


def _budget_limit_set(agent_id: str, scope: str, limit_usd: float) -> str:
    try:
        from devices.inference.budget_ledger import budget_limit_set

        ok = budget_limit_set(agent_id, scope, limit_usd)
    except Exception as exc:
        return f"budget_limit_set error: {exc}"
    if ok:
        return f"Budget limit set: {agent_id!r} {scope}=${limit_usd:.4f}"
    return "Failed to set budget limit — DB unavailable."


def _budget_remaining(agent_id: str, session_id: str) -> str:
    try:
        from devices.inference.budget_ledger import budget_remaining

        info = budget_remaining(agent_id, session_id)
    except Exception as exc:
        return f"budget_remaining error: {exc}"
    if info["limit_usd"] is None:
        return (
            f"No session limit set for {agent_id!r}. "
            f"Spent so far: ${info['spent_usd']:.4f}"
        )
    return (
        f"Budget for {agent_id!r} session={session_id!r}: "
        f"${info['remaining_usd']:.4f} remaining "
        f"({info['pct_used']:.1f}% used of ${info['limit_usd']:.4f} limit)"
    )


def dispatch(name: str, args: dict) -> str | None:
    _handlers = {
        "budget_summary":          lambda a: _budget_summary(a["agent_id"], a.get("group_by", "session")),
        "budget_limit_set":        lambda a: _budget_limit_set(a["agent_id"], a["scope"], float(a["limit_usd"])),
        "budget_remaining":        lambda a: _budget_remaining(a["agent_id"], a["session_id"]),
        "check_openrouter_balance": lambda a: _check_openrouter_balance(),
        "openrouter_burn_rate":    lambda a: _openrouter_burn_rate(float(a.get("window_hours", 48.0))),
    }
    handler = _handlers.get(name)
    return handler(args) if handler else None
