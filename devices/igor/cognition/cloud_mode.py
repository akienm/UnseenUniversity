"""
cloud_mode.py — Master gate for cloud-inference-as-training mode.

is_cloud_training_active() returns True when ALL THREE hold:
  1. IGOR_CLOUD_TRAINING_ENABLED=true in env
  2. OpenRouter account balance >= IGOR_CLOUD_BUDGET_FLOOR_USD (default $10.00)
  3. Local time is 06:00–22:59 (daytime; protect overnight quiet)

Result is cached for 5 minutes to avoid hammering the OR balance API.
"""

from __future__ import annotations

import json
import os
import time
import threading
import datetime
from pathlib import Path
from typing import Optional

from ..paths import paths

_lock = threading.Lock()
_cache_result: Optional[bool] = None
_cache_time: float = 0.0
_CACHE_TTL = 300.0  # 5 minutes


_BALANCE_UNKNOWN = -1.0  # sentinel: API error — do not treat as zero


def _or_balance() -> float:
    """Fetch OpenRouter credit balance via their API.
    Returns the balance, 999.0 for prepaid/unlimited, or _BALANCE_UNKNOWN on error.
    Never returns 0.0 for a network/parse failure — that would silently disable cloud.
    """
    from ..config import get as _cfg_get

    api_key = _cfg_get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return _BALANCE_UNKNOWN
    try:
        import urllib.request as _ur
        import json as _json

        req = _ur.Request(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {api_key}"},
            method="GET",
        )
        with _ur.urlopen(req, timeout=10) as resp:
            data = _json.loads(resp.read())
        # OR returns {"data": {"usage": ..., "limit": ..., "is_free_tier": ...}}
        # Balance = limit - usage (limit=None for unlimited/prepaid)
        d = data.get("data", {})
        limit = d.get("limit")
        usage = d.get("usage", 0.0) or 0.0
        if limit is None:
            # Prepaid / unlimited — treat as always funded
            return 999.0
        return max(0.0, float(limit) - float(usage))
    except Exception as e:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "[cloud_mode] OR balance API call failed: %s: %s",
            type(e).__name__,
            e,
        )
        return _BALANCE_UNKNOWN


def _is_daytime() -> bool:
    """Return True if local hour is 06:00–22:59."""
    hour = datetime.datetime.now().hour
    return 6 <= hour <= 22


def is_cloud_training_active() -> bool:
    """
    Master switch: should Igor prefer cloud inference for training purposes?

    Cached 5 minutes. Reads:
      IGOR_CLOUD_TRAINING_ENABLED (bool, default false)
      IGOR_CLOUD_BUDGET_FLOOR_USD (float, default 10.00)
    """
    global _cache_result, _cache_time

    with _lock:
        now = time.monotonic()
        if _cache_result is not None and (now - _cache_time) < _CACHE_TTL:
            return _cache_result

        result = _compute()
        _cache_result = result
        _cache_time = now
        return result


def _compute() -> bool:
    # Condition 1: env var enabled
    if os.getenv("IGOR_CLOUD_TRAINING_ENABLED", "false").lower() not in (
        "1",
        "true",
        "yes",
    ):
        return False

    # Condition 3: daytime (cheap check first to avoid balance API hit at night)
    if not _is_daytime():
        return False

    # Condition 2: balance above floor
    floor = float(os.getenv("IGOR_CLOUD_BUDGET_FLOOR_USD", "10.00"))
    balance = _or_balance()
    if balance == _BALANCE_UNKNOWN:
        # API unreachable — assume funded rather than silently disabling cloud
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "[cloud_mode] OR balance check failed — assuming funded"
        )
        return True
    return balance >= floor


def invalidate_cache() -> None:
    """Force next call to re-evaluate (e.g. after .env reload)."""
    global _cache_result
    with _lock:
        _cache_result = None


# ── Runtime cloud_ok override ──────────────────────────────────────────────────
# Separate from is_cloud_training_active() — this is a human-triggered or
# habit-triggered override that allows cloud inference at any time (including night).
# Stored as a file so background subprocesses can read it without restart.

_OVERRIDE_FILE = paths().cloud_ok_override


def is_cloud_ok_override() -> bool:
    """
    Return True if a cloud_ok override is currently active (not expired).
    Written by PROC_SET_CLOUD_NOW habit ("do it now"); read per-call by
    inference_gateway and book_learner. Checked without lock — reads are atomic
    at filesystem level for small JSON files.
    """
    try:
        if not _OVERRIDE_FILE.exists():
            return False
        data = json.loads(_OVERRIDE_FILE.read_text())
        if not data.get("active", False):
            return False
        expires = data.get("expires")
        if expires:
            exp_dt = datetime.datetime.fromisoformat(expires)
            if datetime.datetime.now() > exp_dt:
                # Expired — clean up
                _OVERRIDE_FILE.unlink(missing_ok=True)
                return False
        return True
    except Exception:
        return False


def set_cloud_ok_override(ttl_hours: float = 4.0, reason: str = "") -> str:
    """
    Activate cloud_ok override for ttl_hours (default 4h).
    Called by PROC_SET_CLOUD_NOW habit or 'do it now' commands.
    Returns a status string.
    """
    try:
        _OVERRIDE_FILE.parent.mkdir(parents=True, exist_ok=True)
        expires = (
            datetime.datetime.now() + datetime.timedelta(hours=ttl_hours)
        ).isoformat()
        data = {
            "active": True,
            "expires": expires,
            "set_by": reason or "habit",
            "ttl_hours": ttl_hours,
        }
        _OVERRIDE_FILE.write_text(json.dumps(data))
        invalidate_cache()
        return f"cloud_ok override active for {ttl_hours}h (expires {expires[:16]})."
    except Exception as exc:
        return f"cloud_ok override failed: {exc}"


def clear_cloud_ok_override(reason: str = "") -> str:
    """
    Deactivate cloud_ok override. Called by PROC_NIGHT_READ or time-based habits.
    Returns a status string.
    """
    try:
        existed = _OVERRIDE_FILE.exists()
        _OVERRIDE_FILE.unlink(missing_ok=True)
        invalidate_cache()
        return (
            "cloud_ok override cleared."
            if existed
            else "cloud_ok override was not set."
        )
    except Exception as exc:
        return f"cloud_ok clear failed: {exc}"
