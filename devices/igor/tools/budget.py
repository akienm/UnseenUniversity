"""
Budget tracker for Claude API spend.

Persists spend data in SQLite alongside the main memory DB.
Used by AnthropicReasoner to:
  1. Record cost after each API call.
  2. Check remaining budget BEFORE each call.
  3. Alert interruptors when budget runs low.

Also exposed as a tool so Igor can query/set budget mid-conversation.
"""

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from .registry import Tool, registry

# ── Config ──────────────────────────────────────────────────────────────────
# Default monthly budget cap in USD. Override with IGOR_CLAUDE_BUDGET env var.
DEFAULT_BUDGET_USD = 10.00

# Alert threshold — interruptor fires when remaining drops below this fraction.
WARN_FRACTION = 0.20   # warn at 20% remaining
CRITICAL_USD   = 2.00  # hard "keep it down" threshold in dollars


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


# ── Core API ─────────────────────────────────────────────────────────────────

def get_budget() -> float:
    """Return the current budget cap (USD)."""
    with _conn() as c:
        row = c.execute("SELECT value FROM config WHERE key='budget_usd'").fetchone()
    if row:
        return float(row["value"])
    return float(os.getenv("IGOR_CLAUDE_BUDGET", DEFAULT_BUDGET_USD))


def set_budget(usd: float) -> str:
    """Set a new budget cap. Returns confirmation string."""
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO config (key, value) VALUES ('budget_usd', ?)",
            (str(usd),)
        )
    return f"Budget set to ${usd:.2f}"


def get_spend_total() -> float:
    """Return total spend recorded (USD)."""
    with _conn() as c:
        row = c.execute("SELECT COALESCE(SUM(usd), 0) as total FROM spend").fetchone()
    return float(row["total"])


def get_remaining() -> float:
    """Return remaining budget (USD). May be negative if over-budget."""
    return get_budget() - get_spend_total()


def record_spend(usd: float, model: str = "unknown", note: str = "") -> None:
    """Record a spend event. Called by AnthropicReasoner after each API call."""
    with _conn() as c:
        c.execute(
            "INSERT INTO spend (timestamp, model, usd, note) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(), model, usd, note)
        )


def budget_status() -> dict:
    """Return a dict with all budget info."""
    budget = get_budget()
    spent  = get_spend_total()
    remaining = budget - spent
    pct_used = (spent / budget * 100) if budget > 0 else 100
    return {
        "budget_usd":    budget,
        "spent_usd":     spent,
        "remaining_usd": remaining,
        "pct_used":      pct_used,
        "warn":          remaining < (budget * WARN_FRACTION),
        "critical":      remaining < CRITICAL_USD,
    }


def check_before_call() -> tuple[bool, str]:
    """
    Call this BEFORE making a Claude API call.
    Returns (ok_to_call: bool, message: str).
    If ok=False, the call should be skipped or redirected to a cheaper model.
    """
    s = budget_status()
    if s["remaining_usd"] <= 0:
        return False, (
            f"⛔ Budget exhausted! Spent ${s['spent_usd']:.2f} of ${s['budget_usd']:.2f}. "
            "Cannot make Claude API call. Let Akien know to top up."
        )
    if s["critical"]:
        return True, (
            f"⚠️  BUDGET CRITICAL: Only ${s['remaining_usd']:.2f} remaining "
            f"(${s['budget_usd']:.2f} cap, {s['pct_used']:.0f}% used). "
            "Proceeding but please notify Akien!"
        )
    if s["warn"]:
        return True, (
            f"⚡ Budget low: ${s['remaining_usd']:.2f} remaining "
            f"({100 - s['pct_used']:.0f}% left of ${s['budget_usd']:.2f})."
        )
    return True, f"Budget OK: ${s['remaining_usd']:.2f} remaining."


# ── Tool functions (exposed to Claude) ───────────────────────────────────────

def _tool_check_budget(**_) -> str:
    ok, msg = check_before_call()
    s = budget_status()
    return (
        f"{msg}\n"
        f"Budget: ${s['budget_usd']:.2f} | "
        f"Spent: ${s['spent_usd']:.4f} | "
        f"Remaining: ${s['remaining_usd']:.4f}"
    )


def _tool_set_budget(amount_usd: float, **_) -> str:
    return set_budget(float(amount_usd))


def _tool_spend_history(limit: int = 20, **_) -> str:
    with _conn() as c:
        rows = c.execute(
            "SELECT timestamp, model, usd, note FROM spend ORDER BY id DESC LIMIT ?",
            (int(limit),)
        ).fetchall()
    if not rows:
        return "No spend recorded yet."
    lines = ["Recent Claude API spend (newest first):"]
    for r in rows:
        ts = r["timestamp"][:16]
        lines.append(f"  {ts}  {r['model']:<35}  ${r['usd']:.4f}  {r['note']}")
    s = budget_status()
    lines.append(f"\nTotal: ${s['spent_usd']:.4f} / ${s['budget_usd']:.2f} budget")
    return "\n".join(lines)


# ── Register tools ────────────────────────────────────────────────────────────

registry.register(Tool(
    name="check_claude_budget",
    description=(
        "Check remaining Claude API budget. Shows how much has been spent and what's left. "
        "Call this before making expensive requests when budget is a concern."
    ),
    parameters={
        "type": "object",
        "properties": {},
        "required": [],
    },
    fn=_tool_check_budget,
))

registry.register(Tool(
    name="set_claude_budget",
    description="Set the Claude API budget cap in USD. Use this when Akien tops up the budget.",
    parameters={
        "type": "object",
        "properties": {
            "amount_usd": {
                "type": "number",
                "description": "New budget cap in US dollars (e.g. 10.00)",
            },
        },
        "required": ["amount_usd"],
    },
    fn=_tool_set_budget,
))

registry.register(Tool(
    name="claude_spend_history",
    description="Show recent Claude API spend history.",
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
