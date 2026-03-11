"""
Budget tracker for OpenRouter API spend.

Persists spend data in SQLite alongside the main memory DB.
Used by reasoners to:
  1. Record cost after each API call.
  2. Check remaining budget BEFORE each call.
  3. Alert interruptors when budget runs low.

Real balance is fetched from the OpenRouter API (GET /api/v1/credits),
cached for one hour so we don't spam the endpoint.

Igor CANNOT purchase credits. Only Akien manages account funding.
"""

import json
import os
import sqlite3
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from .registry import Tool, registry

# ── Config ──────────────────────────────────────────────────────────────────
# Soft spending cap (USD) — a local guardrail, not account balance.
# Override with IGOR_SPENDING_CAP env var.
DEFAULT_SPENDING_CAP_USD = 10.00

# Alert threshold — interruptor fires when remaining drops below this fraction.
WARN_FRACTION = 0.20   # warn at 20% remaining
CRITICAL_USD   = 2.00  # hard "keep it down" threshold in dollars

# OpenRouter credits endpoint
_OR_CREDITS_URL = "https://openrouter.ai/api/v1/credits"

# Cache real balance for 1 hour
_BALANCE_CACHE_TTL_SEC = 3600
_balance_cache: dict = {}   # keys: purchased, used, balance, fetched_at


# ── DB path (same directory as main memory DB) ────────────────────────────
def _db_path() -> Path:
    base = os.getenv("IGOR_DB_PATH", "memory/igor.db")
    return Path(base).parent / "claude_budget.db"


def _conn():
    db = _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE IF NOT EXISTS spend (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT    NOT NULL,
            model     TEXT    NOT NULL,
            usd       REAL    NOT NULL,
            note      TEXT    DEFAULT ''
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS config (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    c.commit()
    return c


# ── Real balance from OpenRouter API ─────────────────────────────────────────

def fetch_openrouter_balance() -> dict | None:
    """
    Fetch real account balance from OpenRouter API. Cached for 1 hour.

    Returns dict: {purchased, used, balance, fetched_at} or None on error.
    Uses OPENROUTER_MANAGEMENT_KEY if set, falls back to OPENROUTER_API_KEY.
    """
    global _balance_cache
    now = time.time()
    if _balance_cache and (now - _balance_cache.get("fetched_at", 0)) < _BALANCE_CACHE_TTL_SEC:
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
            "used":      float(data["total_usage"]),
            "balance":   float(data["total_credits"]) - float(data["total_usage"]),
            "fetched_at": now,
        }
        _balance_cache = result
        return result
    except Exception:
        return None


# ── Local spending cap (soft guardrail) ───────────────────────────────────────

def get_spending_cap() -> float:
    """Return the local spending cap (USD). Not the same as account balance."""
    with _conn() as c:
        row = c.execute("SELECT value FROM config WHERE key='spending_cap_usd'").fetchone()
    if row:
        return float(row["value"])
    return float(os.getenv("IGOR_SPENDING_CAP", DEFAULT_SPENDING_CAP_USD))


def set_spending_cap(usd: float) -> str:
    """Set local spending cap. Returns confirmation string."""
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('spending_cap_usd', ?)",
            (str(usd),)
        )
    return f"Local spending cap set to ${usd:.2f}"


def get_spend_total() -> float:
    """Return total spend recorded locally (USD)."""
    with _conn() as c:
        row = c.execute("SELECT COALESCE(SUM(usd), 0) as total FROM spend").fetchone()
    return float(row["total"])


def get_remaining() -> float:
    """Return remaining vs local cap (USD). May be negative if over cap."""
    return get_spending_cap() - get_spend_total()


def record_spend(usd: float, model: str = "unknown", note: str = "") -> None:
    """Record a spend event. Called by reasoners after each API call."""
    with _conn() as c:
        c.execute(
            "INSERT INTO spend (timestamp, model, usd, note) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(), model, usd, note)
        )


def budget_status() -> dict:
    """
    Return a dict with all budget info.

    Prefers real OpenRouter API balance when available (cached ≤1h).
    Falls back to local spend-tracking against cap.
    """
    real = fetch_openrouter_balance()
    cap  = get_spending_cap()
    spent_local = get_spend_total()

    if real:
        remaining = real["balance"]
        # Warn thresholds relative to purchased amount
        total_purchased = real["purchased"]
        pct_used = (real["used"] / total_purchased * 100) if total_purchased > 0 else 0
        return {
            "source":        "openrouter_api",
            "balance_usd":   real["balance"],
            "purchased_usd": real["purchased"],
            "used_usd":      real["used"],
            "remaining_usd": remaining,
            "pct_used":      pct_used,
            "spending_cap":  cap,
            "local_spent":   spent_local,
            "fetched_at":    real["fetched_at"],
            "warn":          remaining < (total_purchased * WARN_FRACTION),
            "critical":      remaining < CRITICAL_USD,
        }
    else:
        # Fallback: local tracking only
        remaining = cap - spent_local
        pct_used = (spent_local / cap * 100) if cap > 0 else 100
        return {
            "source":        "local_tracking",
            "remaining_usd": remaining,
            "spending_cap":  cap,
            "local_spent":   spent_local,
            "pct_used":      pct_used,
            "warn":          remaining < (cap * WARN_FRACTION),
            "critical":      remaining < CRITICAL_USD,
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


# ── Tool functions (exposed to Igor) ─────────────────────────────────────────

def _tool_check_balance(**_) -> str:
    s = budget_status()
    if s["source"] == "openrouter_api":
        age_min = (time.time() - s["fetched_at"]) / 60
        return (
            f"OpenRouter account balance (live, fetched {age_min:.0f}m ago):\n"
            f"  Purchased: ${s['purchased_usd']:.2f}\n"
            f"  Used:      ${s['used_usd']:.4f}\n"
            f"  Remaining: ${s['remaining_usd']:.4f}\n"
            f"  Local cap: ${s['spending_cap']:.2f} | Local tracked: ${s['local_spent']:.4f}"
        )
    else:
        return (
            f"OpenRouter balance (local tracking — API unavailable):\n"
            f"  Remaining vs cap: ${s['remaining_usd']:.4f} of ${s['spending_cap']:.2f}"
        )


def _tool_set_spending_cap(amount_usd: float, **_) -> str:
    return set_spending_cap(float(amount_usd))


def _tool_spend_history(limit: int = 20, **_) -> str:
    with _conn() as c:
        rows = c.execute(
            "SELECT timestamp, model, usd, note FROM spend ORDER BY id DESC LIMIT ?",
            (int(limit),)
        ).fetchall()
    if not rows:
        return "No spend recorded yet."
    lines = ["Recent OpenRouter spend (newest first):"]
    for r in rows:
        ts = r["timestamp"][:16]
        lines.append(f"  {ts}  {r['model']:<35}  ${r['usd']:.4f}  {r['note']}")
    s = budget_status()
    lines.append(f"\nLocal total: ${s['local_spent']:.4f}")
    return "\n".join(lines)


# ── Register tools ────────────────────────────────────────────────────────────

registry.register(Tool(
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
))

registry.register(Tool(
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
))

registry.register(Tool(
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
))

# Alias: models commonly hallucinate this name; redirect to the real tool.
registry.register(Tool(
    name="get_budget_status",
    description="Alias for check_openrouter_balance. Use that instead.",
    parameters={"type": "object", "properties": {}, "required": []},
    fn=_tool_check_balance,
))
