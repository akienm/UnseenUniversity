"""
budget.py — UC canonical home for OpenRouter spend tracking (T-uc-budget-shelf).

Persists spend data in a shelf alongside the main memory DB (via DatabaseProxy).
Used by reasoners to:
  1. Record cost after each API call.
  2. Check remaining budget BEFORE each call.
  3. Alert interruptors when budget runs low.

Real account balance is fetched from the OpenRouter API
(GET /api/v1/credits), cached for one hour so we don't spam the endpoint.

Igor CANNOT purchase credits. Only Akien manages account funding.

## Layer policy

Canonical implementation lives here in UC. `wild_igor/igor/tools/budget.py`
is a thin re-export shim — existing `from ..tools.budget import …` imports
keep working.

Cross-layer wild_igor imports are lazy (inside functions), so UC never
triggers wild_igor package load at import time. That was the blocker
the earlier partial shim faced; with registry + db_proxy now in UC, the
wild_igor dependencies are narrowed to `paths()` and `log_error` — both
called inside functions.
"""

import json
import os
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional

from .registry import Tool, registry
from ..memory.db_proxy import make_home_proxy

# ── Config ──────────────────────────────────────────────────────────────────
# Soft spending cap (USD) — a local guardrail, not account balance.
# Override with IGOR_SPENDING_CAP env var.
DEFAULT_SPENDING_CAP_USD = 10.00

# Alert threshold — interruptor fires when remaining drops below this fraction.
WARN_FRACTION = 0.20  # warn at 20% remaining
CRITICAL_USD = 2.00  # hard "keep it down" threshold in dollars

# OpenRouter credits endpoint
_OR_CREDITS_URL = "https://openrouter.ai/api/v1/credits"

# Cache real balance for 1 hour
_BALANCE_CACHE_TTL_SEC = 3600
_balance_cache: dict = {}  # keys: purchased, used, balance, fetched_at


_BUDGET_PROXY = None


def _db_proxy():
    """Singleton home-db proxy for budget tables.

    Tables live in infra.* on the home_db (per-Igor operational
    infrastructure, alongside sessions/slates/decisions/machines/metrics).
    Schema is created by cortex migration m053; we don't run CREATE TABLE
    here. make_home_proxy's default search_path includes infra so bare
    names (spend, budget_config, balance_history) resolve naturally.
    """
    global _BUDGET_PROXY
    if _BUDGET_PROXY is None:
        _BUDGET_PROXY = make_home_proxy()
    return _BUDGET_PROXY


def log_error(kind: str, detail: str) -> None:
    """Forensic log passthrough — lazy import to keep UC clean of wild_igor at load."""
    try:
        from unseen_university.devices.igor.cognition.forensic_logger import log_error as _le

        _le(kind=kind, detail=detail)
    except Exception:
        pass


# ── Real balance from OpenRouter API ─────────────────────────────────────────


def fetch_openrouter_balance() -> dict | None:
    """
    Fetch real account balance from OpenRouter API. Cached for 1 hour.

    Returns dict: {purchased, used, balance, fetched_at} or None on error.
    Uses OPENROUTER_MANAGEMENT_KEY if set, falls back to OPENROUTER_API_KEY.
    """
    global _balance_cache
    now = time.time()
    if (
        _balance_cache
        and (now - _balance_cache.get("fetched_at", 0)) < _BALANCE_CACHE_TTL_SEC
    ):
        return _balance_cache.copy()

    api_key = os.getenv("OPENROUTER_MANAGEMENT_KEY") or os.getenv("OPENROUTER_API_KEY")
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
        _balance_cache = result
        # Persist to history for burn-rate tracking (one row per cache refresh)
        try:
            with _db_proxy()() as c:
                c.execute(
                    "INSERT INTO balance_history (timestamp, balance, purchased, used) VALUES (%s, %s, %s, %s)",
                    (
                        datetime.fromtimestamp(now).isoformat(),
                        result["balance"],
                        result["purchased"],
                        result["used"],
                    ),
                )
        except Exception as e:
            log_error(kind="TOOL_FAIL", detail=f"history write failed: {e}")
        return result
    except Exception:
        return None


# ── Balance history + burn trajectory ────────────────────────────────────────


def get_balance_trajectory(window_hours: float = 24.0) -> dict:
    """
    Compute OR balance burn rate from stored history.

    Returns:
        burn_per_day: float    — USD/day at current rate (positive = spending)
        days_remaining: float  — at this rate, days until $0 (inf if no burn)
        balance_now: float     — most recent balance reading
        oldest_sample_age_h: float — age of oldest sample used
        sample_count: int      — number of history rows in window
        trend: str             — "burning_fast" | "burning" | "stable" | "no_data"
    """
    try:
        cutoff = datetime.fromtimestamp(time.time() - window_hours * 3600).isoformat()
        with _db_proxy()() as c:
            rows = c.execute(
                "SELECT timestamp, balance FROM balance_history WHERE timestamp >= %s ORDER BY timestamp ASC",
                (cutoff,),
            ).fetchall()
    except Exception:
        return {
            "trend": "no_data",
            "burn_per_day": 0.0,
            "days_remaining": float("inf"),
            "balance_now": 0.0,
            "oldest_sample_age_h": 0.0,
            "sample_count": 0,
        }

    if len(rows) < 2:
        # Single or no sample — no trajectory yet
        balance_now = rows[0]["balance"] if rows else 0.0
        return {
            "trend": "no_data",
            "burn_per_day": 0.0,
            "days_remaining": float("inf"),
            "balance_now": balance_now,
            "oldest_sample_age_h": 0.0,
            "sample_count": len(rows),
        }

    # Postgres TIMESTAMPTZ returns datetime; legacy SQLite stored as ISO string.
    # Accept both — migration script may have left mixed shapes briefly.
    def _to_ts(v):
        return (
            v.timestamp()
            if isinstance(v, datetime)
            else datetime.fromisoformat(v).timestamp()
        )

    t0 = _to_ts(rows[0]["timestamp"])
    t1 = _to_ts(rows[-1]["timestamp"])
    b0 = rows[0]["balance"]
    b1 = rows[-1]["balance"]

    elapsed_days = (t1 - t0) / 86400.0
    if elapsed_days < 1e-6:
        return {
            "trend": "no_data",
            "burn_per_day": 0.0,
            "days_remaining": float("inf"),
            "balance_now": b1,
            "oldest_sample_age_h": 0.0,
            "sample_count": len(rows),
        }

    burn_per_day = (b0 - b1) / elapsed_days  # positive = spending
    days_remaining = (b1 / burn_per_day) if burn_per_day > 0.001 else float("inf")
    oldest_age_h = (time.time() - t0) / 3600.0

    if burn_per_day > 20:
        trend = "burning_fast"
    elif burn_per_day > 5:
        trend = "burning"
    else:
        trend = "stable"

    return {
        "trend": trend,
        "burn_per_day": round(burn_per_day, 2),
        "days_remaining": (
            round(days_remaining, 1) if days_remaining != float("inf") else float("inf")
        ),
        "balance_now": round(b1, 2),
        "oldest_sample_age_h": round(oldest_age_h, 1),
        "sample_count": len(rows),
    }


# ── Local spending cap (soft guardrail) ───────────────────────────────────────


def get_spending_cap() -> float:
    """Return the local spending cap (USD). Not the same as account balance."""
    with _db_proxy()() as c:
        row = c.execute(
            "SELECT value FROM budget_config WHERE key='spending_cap_usd'"
        ).fetchone()
    if row:
        return float(row["value"])
    return float(os.getenv("IGOR_SPENDING_CAP", DEFAULT_SPENDING_CAP_USD))


def set_spending_cap(usd: float) -> str:
    """Set local spending cap. Returns confirmation string."""
    with _db_proxy()() as c:
        c.execute(
            "INSERT INTO budget_config (key, value) VALUES ('spending_cap_usd', %s) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (str(usd),),
        )
    return f"Local spending cap set to ${usd:.2f}"


def get_spend_total() -> float:
    """Return total spend recorded locally (USD)."""
    with _db_proxy()() as c:
        row = c.execute("SELECT COALESCE(SUM(usd), 0) as total FROM spend").fetchone()
    return float(row["total"])


def get_remaining() -> float:
    """Return remaining vs local cap (USD). May be negative if over cap."""
    return get_spending_cap() - get_spend_total()


def record_spend(usd: float, model: str = "unknown", note: str = "") -> None:
    """Record a spend event. Called by reasoners after each API call."""
    with _db_proxy()() as c:
        c.execute(
            "INSERT INTO spend (timestamp, model, usd, note) VALUES (%s, %s, %s, %s)",
            (datetime.now().isoformat(), model, usd, note),
        )


def budget_status() -> dict:
    """
    Return a dict with all budget info.

    Prefers real OpenRouter API balance when available (cached ≤1h).
    Falls back to local spend-tracking against cap.
    """
    real = fetch_openrouter_balance()
    cap = get_spending_cap()
    spent_local = get_spend_total()

    if real:
        remaining = real["balance"]
        # Warn thresholds relative to purchased amount
        total_purchased = real["purchased"]
        pct_used = (real["used"] / total_purchased * 100) if total_purchased > 0 else 0
        return {
            "source": "openrouter_api",
            "balance_usd": real["balance"],
            "purchased_usd": real["purchased"],
            "used_usd": real["used"],
            "remaining_usd": remaining,
            "pct_used": pct_used,
            "spending_cap": cap,
            "local_spent": spent_local,
            "fetched_at": real["fetched_at"],
            "warn": remaining < 10.0,
            "critical": remaining < CRITICAL_USD,
        }
    else:
        # Fallback: local tracking only
        remaining = cap - spent_local
        pct_used = (spent_local / cap * 100) if cap > 0 else 100
        return {
            "source": "local_tracking",
            "remaining_usd": remaining,
            "spending_cap": cap,
            "local_spent": spent_local,
            "pct_used": pct_used,
            "warn": remaining < 10.0,
            "critical": remaining < CRITICAL_USD,
        }


def is_cloud_blocked() -> tuple[bool, str]:
    """
    Single check combining floor guard + zero-balance guard.
    Returns (blocked: bool, reason: str).

    blocked=True means: do NOT attempt any cloud API call.
    Route to local inference instead.
    """
    floor = float(os.getenv("IGOR_CLOUD_BUDGET_FLOOR_USD", "0.0"))
    s = budget_status()
    remaining = s["remaining_usd"]

    if remaining <= 0:
        return True, (
            f"OpenRouter balance exhausted (${remaining:.2f}). "
            "Running on local inference until Akien tops up credits."
        )
    if floor > 0 and remaining <= floor:
        return True, (
            f"Budget floor ${floor:.2f} reached (${remaining:.2f} remaining). "
            "Running on local inference to preserve buffer."
        )
    return False, ""


def check_budget_floor() -> tuple[bool, str]:
    """
    Check whether remaining balance is above the configured research floor.
    Returns (ok_to_call: bool, message: str).

    Floor set by IGOR_CLOUD_BUDGET_FLOOR_USD (default 0.0 = disabled).
    When remaining drops below the floor, cloud inference stops gracefully
    so a buffer is preserved for interactive / non-research tasks.
    """
    floor = float(os.getenv("IGOR_CLOUD_BUDGET_FLOOR_USD", "0.0"))
    if floor <= 0.0:
        return True, ""
    s = budget_status()
    remaining = s["remaining_usd"]
    if remaining <= floor:
        return False, (
            f"📚 Budget floor ${floor:.2f} reached (${remaining:.2f} remaining). "
            "Stopping cloud inference to preserve buffer for other tasks. "
            "Lower IGOR_CLOUD_BUDGET_FLOOR_USD or ask Akien to add credits."
        )
    return True, ""


def check_before_call() -> tuple[bool, str]:
    """
    Call this BEFORE making an OpenRouter API call.
    Returns (ok_to_call: bool, message: str).
    """
    s = budget_status()
    remaining = s["remaining_usd"]
    if remaining <= 0:
        src = s["source"]
        return False, (
            f"⛔ Balance exhausted ({src})! ${remaining:.2f} remaining. "
            "Cannot make OpenRouter call. Let Akien know."
        )
    if s["critical"]:
        return True, (
            f"⚠️  BUDGET CRITICAL: Only ${remaining:.2f} remaining "
            f"({s['pct_used']:.0f}% used, source={s['source']}). "
            "Proceeding but notifying Akien!"
        )
    if s["warn"]:
        return True, (
            f"⚡ Budget low: ${remaining:.2f} remaining "
            f"({100 - s['pct_used']:.0f}% left, source={s['source']})."
        )
    return True, f"Budget OK: ${remaining:.2f} remaining (source={s['source']})."


# ── costs.log query (T-costs-log) ────────────────────────────────────────────


def query_costs_log(window_days: float = 1.0) -> dict:
    """
    Read costs.log and sum inference spend for the last N days.

    costs.log format: ts|inference|provider|model|tier|cost_usd|tokens_in|tokens_out|caller
    Returns: {total_usd, by_provider, by_tier, by_model, row_count, window_days}
    """
    from unseen_university.devices.igor.paths import paths as _paths
    from datetime import timezone

    log_path = _paths().logs / "costs.log"
    if not log_path.exists():
        return {
            "total_usd": 0.0,
            "by_provider": {},
            "by_tier": {},
            "by_model": {},
            "row_count": 0,
            "window_days": window_days,
            "note": "costs.log not found — no inference calls logged yet",
        }

    cutoff_ts = datetime.now(timezone.utc).timestamp() - window_days * 86400
    total = 0.0
    by_provider: dict[str, float] = {}
    by_tier: dict[str, float] = {}
    by_model: dict[str, float] = {}
    row_count = 0

    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("|")
                if len(parts) < 7:
                    continue
                try:
                    # ts field: ISO format with timezone
                    ts_str = parts[0]
                    ts = datetime.fromisoformat(
                        ts_str.replace("Z", "+00:00")
                    ).timestamp()
                    if ts < cutoff_ts:
                        continue
                    provider = parts[2]
                    model = parts[3]
                    tier = parts[4]
                    cost = float(parts[5])
                    total += cost
                    by_provider[provider] = by_provider.get(provider, 0.0) + cost
                    by_tier[tier] = by_tier.get(tier, 0.0) + cost
                    by_model[model] = by_model.get(model, 0.0) + cost
                    row_count += 1
                except (ValueError, IndexError):
                    continue
    except Exception as e:
        return {
            "total_usd": 0.0,
            "by_provider": {},
            "by_tier": {},
            "by_model": {},
            "row_count": 0,
            "window_days": window_days,
            "note": f"read error: {e}",
        }

    return {
        "total_usd": round(total, 6),
        "by_provider": {
            k: round(v, 6) for k, v in sorted(by_provider.items(), key=lambda x: -x[1])
        },
        "by_tier": {
            k: round(v, 6) for k, v in sorted(by_tier.items(), key=lambda x: -x[1])
        },
        "by_model": {
            k: round(v, 6) for k, v in sorted(by_model.items(), key=lambda x: -x[1])
        },
        "row_count": row_count,
        "window_days": window_days,
    }


# ── Tool functions (exposed to Igor) ─────────────────────────────────────────


def _tool_check_balance(**_) -> str:
    s = budget_status()
    traj = get_balance_trajectory()
    if s["source"] == "openrouter_api":
        age_min = (time.time() - s["fetched_at"]) / 60
        lines = [
            f"OpenRouter account balance (live, fetched {age_min:.0f}m ago):",
            f"  Purchased: ${s['purchased_usd']:.2f}",
            f"  Used:      ${s['used_usd']:.4f}",
            f"  Remaining: ${s['remaining_usd']:.4f}",
            f"  Local cap: ${s['spending_cap']:.2f} | Local tracked: ${s['local_spent']:.4f}",
        ]
        if traj["trend"] != "no_data":
            dr = traj["days_remaining"]
            dr_str = f"{dr:.1f}d" if dr != float("inf") else "∞"
            lines.append(
                f"  Burn rate: ${traj['burn_per_day']:.2f}/day ({traj['trend']}) — "
                f"~{dr_str} remaining at this rate  [{traj['sample_count']} samples, {traj['oldest_sample_age_h']:.0f}h window]"
            )
        else:
            lines.append(
                "  Burn rate: insufficient history (will populate over next few hours)"
            )
        return "\n".join(lines)
    else:
        return (
            f"OpenRouter balance (local tracking — API unavailable):\n"
            f"  Remaining vs cap: ${s['remaining_usd']:.4f} of ${s['spending_cap']:.2f}"
        )


def _tool_balance_trajectory(window_hours: float = 48.0, **_) -> str:
    """Show OR balance burn trajectory over the last N hours."""
    traj = get_balance_trajectory(float(window_hours))
    if traj["trend"] == "no_data":
        return (
            f"Insufficient balance history to compute trajectory "
            f"({traj['sample_count']} sample(s) in last {window_hours:.0f}h window). "
            "History populates as Igor fetches balance (once per hour)."
        )
    dr = traj["days_remaining"]
    dr_str = (
        f"{dr:.1f} days" if dr != float("inf") else "unlimited (no meaningful burn)"
    )
    return (
        f"OR Balance Trajectory ({traj['oldest_sample_age_h']:.0f}h window, {traj['sample_count']} samples):\n"
        f"  Current balance: ${traj['balance_now']:.2f}\n"
        f"  Burn rate:       ${traj['burn_per_day']:.2f}/day  ({traj['trend']})\n"
        f"  Days remaining:  {dr_str}"
    )


def _tool_set_spending_cap(amount_usd: float, **_) -> str:
    return set_spending_cap(float(amount_usd))


def _tool_spend_history(limit: int = 20, **_) -> str:
    with _db_proxy()() as c:
        rows = c.execute(
            "SELECT timestamp, model, usd, note FROM spend ORDER BY id DESC LIMIT %s",
            (int(limit),),
        ).fetchall()
    if not rows:
        return "No spend recorded yet."
    lines = ["Recent OpenRouter spend (newest first):"]
    for r in rows:
        # TIMESTAMPTZ from Postgres returns datetime; SQLite returned ISO str.
        raw = r["timestamp"]
        ts = raw.isoformat()[:16] if isinstance(raw, datetime) else raw[:16]
        lines.append(f"  {ts}  {r['model']:<35}  ${r['usd']:.4f}  {r['note']}")
    s = budget_status()
    lines.append(f"\nLocal total: ${s['local_spent']:.4f}")
    return "\n".join(lines)


# ── Register tools ────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="check_openrouter_balance",
        description=(
            "Check real OpenRouter account balance by polling the API (cached 1 hour). "
            "Use this instead of guessing. Do NOT use this more than once per hour. "
            "You cannot purchase credits — only Akien manages that."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=_tool_check_balance,
    )
)

registry.register(
    Tool(
        name="set_spending_cap",
        description=(
            "Set a local spending cap (USD) as a soft guardrail. "
            "This does NOT purchase credits — it just sets a local limit. "
            "Use check_openrouter_balance for the real account balance."
        ),
        parameters={
            "type": "object",
            "properties": {
                "amount_usd": {
                    "type": "number",
                    "description": "Spending cap in US dollars (e.g. 10.00)",
                },
            },
            "required": ["amount_usd"],
        },
        fn=_tool_set_spending_cap,
    )
)

registry.register(
    Tool(
        name="openrouter_spend_history",
        description="Show recent locally-tracked OpenRouter spend history.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent entries to show (default 20)",
                },
            },
            "required": [],
        },
        fn=_tool_spend_history,
    )
)

registry.register(
    Tool(
        name="openrouter_balance_trajectory",
        description=(
            "Show OR balance burn rate and days remaining based on historical readings. "
            "Use to answer 'how fast are we spending?' or 'how long will credits last?'. "
            "window_hours: lookback window in hours (default 48)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "window_hours": {
                    "type": "number",
                    "description": "Lookback window in hours (default 48)",
                },
            },
            "required": [],
        },
        fn=_tool_balance_trajectory,
    )
)

# Alias: models commonly hallucinate this name; redirect to the real tool.
registry.register(
    Tool(
        name="get_budget_status",
        description="Alias for check_openrouter_balance. Use that instead.",
        parameters={"type": "object", "properties": {}, "required": []},
        fn=_tool_check_balance,
    )
)


def _tool_costs_log(window_days: float = 1.0, **_) -> str:
    """Show inference spend from costs.log for the last N days."""
    data = query_costs_log(float(window_days))
    if "note" in data and data["row_count"] == 0:
        return data["note"]
    lines = [
        f"Inference costs — last {window_days:.0f}d ({data['row_count']} calls): ${data['total_usd']:.4f}",
    ]
    if data["by_tier"]:
        lines.append(
            "  By tier: "
            + " | ".join(f"{k}=${v:.4f}" for k, v in data["by_tier"].items())
        )
    if data["by_provider"]:
        lines.append(
            "  By provider: "
            + " | ".join(f"{k}=${v:.4f}" for k, v in data["by_provider"].items())
        )
    if data["by_model"]:
        top_models = list(data["by_model"].items())[:5]
        lines.append(
            "  Top models: " + " | ".join(f"{m}=${c:.4f}" for m, c in top_models)
        )
    return "\n".join(lines)


registry.register(
    Tool(
        name="inference_costs_log",
        description=(
            "Show inference spend from costs.log broken down by tier, provider, and model. "
            "window_days: lookback window in days (default 1 = today). "
            "Use 7 for weekly totals. Feeds priority decisions: which tiers burn fastest."
        ),
        parameters={
            "type": "object",
            "properties": {
                "window_days": {
                    "type": "number",
                    "description": "Lookback window in days (default 1)",
                },
            },
            "required": [],
        },
        fn=_tool_costs_log,
    )
)
