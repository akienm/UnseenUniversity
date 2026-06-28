"""
budget_gate.py — Lightweight OR budget gate for the ADC inference device.

No TheIgors imports. Reads OPENROUTER_API_KEY and UU_HOME_DB_URL directly.

Two responsibilities:
  check_balance() — fetch real OR balance (cached 1h in-process).
                    Returns (ok: bool, message: str).
  record_spend()  — append a row to infra.spend for attribution + token counts.
                    No-op when UU_HOME_DB_URL is absent or DB unreachable.

Balance snapshots are also written to infra.balance_history on each cache refresh
so the burn-trajectory query (read by budget_tools.py) stays current.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_OR_CREDITS_URL = "https://openrouter.ai/api/v1/credits"
_BALANCE_CACHE_TTL = 3600.0  # 1 hour
_ALERT_DEDUP_SECS = 6 * 3600  # one alert per 6h max
_ALERT_STAMP = Path(os.environ.get("TMPDIR", "/tmp")) / "adc_budget_alert.stamp"

# In-process cache: {balance, purchased, used, fetched_at}
_cache: dict = {}


def _or_api_key() -> str:
    return (
        os.environ.get("OPENROUTER_MANAGEMENT_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
        or ""
    )


def _home_db_url() -> str:
    return os.environ.get("UU_HOME_DB_URL", "")


def _fetch_balance_raw() -> dict | None:
    """Call OR /api/v1/credits. Returns raw dict or None on error."""
    api_key = _or_api_key()
    if not api_key:
        return None
    try:
        req = urllib.request.Request(
            _OR_CREDITS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())["data"]
        return {
            "purchased": float(data["total_credits"]),
            "used": float(data["total_usage"]),
            "balance": float(data["total_credits"]) - float(data["total_usage"]),
            "fetched_at": time.time(),
        }
    except Exception as exc:
        log.debug("budget_gate: OR balance fetch failed — %s", exc)
        return None


def _maybe_alert(balance: float) -> None:
    """Post a low-balance alert to the channel at most once per 6h."""
    threshold = float(os.environ.get("OR_BUDGET_ALERT_USD", "15.0"))
    if balance > threshold:
        return
    # Dedup: skip if we already alerted within the window
    try:
        if _ALERT_STAMP.exists():
            age = time.time() - _ALERT_STAMP.stat().st_mtime
            if age < _ALERT_DEDUP_SECS:
                return
        _ALERT_STAMP.touch()
    except Exception:
        pass
    msg = (
        f"⚠️  OR budget alert: ${balance:.2f} remaining "
        f"(threshold ${threshold:.0f}). Top up credits or reduce inference usage."
    )
    log.warning("budget_gate: %s", msg)
    # Best-effort channel post via shared DB — non-fatal if unavailable
    try:
        db_url = _home_db_url()
        if db_url:
            import psycopg2

            ts = datetime.now(tz=timezone.utc).isoformat()
            with psycopg2.connect(db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO infra.channel_messages (author, message, ts)"
                        " VALUES (%s, %s, %s)",
                        ("adc_budget_gate", msg, ts),
                    )
    except Exception as _e:
        log.debug("budget_gate: channel post failed — %s", _e)


def _write_balance_history(result: dict) -> None:
    """Append balance snapshot to infra.balance_history and fire alert if balance is low."""
    db_url = _home_db_url()
    if not db_url:
        return
    try:
        import psycopg2

        ts = datetime.fromtimestamp(result["fetched_at"], tz=timezone.utc).isoformat()
        with psycopg2.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO infra.balance_history (timestamp, balance, purchased, used)"
                    " VALUES (%s, %s, %s, %s)",
                    (ts, result["balance"], result["purchased"], result["used"]),
                )
    except Exception as exc:
        log.debug("budget_gate: balance_history write failed — %s", exc)

    _maybe_alert(result["balance"])


def fetch_balance() -> dict | None:
    """Return OR balance, using in-process cache (1h TTL). Side-effect: writes to balance_history on refresh."""
    global _cache
    now = time.time()
    if _cache and (now - _cache.get("fetched_at", 0)) < _BALANCE_CACHE_TTL:
        return _cache.copy()
    result = _fetch_balance_raw()
    if result is not None:
        _cache = result
        _write_balance_history(result)
    return result.copy() if result else None


def check_balance() -> tuple[bool, str]:
    """
    Pre-call gate. Returns (ok: bool, message: str).

    ok=False means: do not attempt the OR call (balance at/below floor).
    When the OR API is unreachable, ok=True (fail-open — don't block on network error).
    """
    result = fetch_balance()
    if result is None:
        # Can't verify — fail open so a network hiccup doesn't block all inference
        return True, "OR balance check unavailable — proceeding"
    balance = result["balance"]
    floor = float(os.environ.get("IGOR_CLOUD_BUDGET_FLOOR_USD", "0.0"))
    if balance <= 0:
        return False, f"OR balance exhausted (${balance:.2f})"
    if floor > 0 and balance <= floor:
        return False, f"OR balance ${balance:.2f} at/below floor ${floor:.2f}"
    return True, f"OK (${balance:.2f} remaining)"


def record_spend(
    model: str,
    input_tokens: int,
    output_tokens: int,
    caller: str = "adc_inference",
) -> None:
    """
    Write an attribution row to infra.spend.
    usd=0.0 (exact cost unknown without model pricing tables).
    Token counts + model are in note for forensic analysis.
    No-op when UU_HOME_DB_URL absent or DB unreachable.
    """
    db_url = _home_db_url()
    if not db_url:
        return
    note = f"caller={caller} in={input_tokens} out={output_tokens}"
    ts = datetime.now(tz=timezone.utc).isoformat()
    try:
        import psycopg2

        with psycopg2.connect(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO infra.spend (timestamp, model, usd, note)"
                    " VALUES (%s, %s, %s, %s)",
                    (ts, model, 0.0, note),
                )
    except Exception as exc:
        log.debug("budget_gate: spend record failed — %s", exc)
