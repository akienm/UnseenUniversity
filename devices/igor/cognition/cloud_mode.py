"""
cloud_mode.py — Master gate for cloud-inference-as-training mode.

is_cloud_training_active() returns True when ALL THREE hold:
  1. IGOR_CLOUD_TRAINING_ENABLED=true in env
  2. OpenRouter account balance >= IGOR_CLOUD_BUDGET_FLOOR_USD (default $10.00)
  3. Local time is 06:00–22:59 (daytime; protect overnight quiet)

Result is cached for 5 minutes to avoid hammering the OR balance API.
"""
from __future__ import annotations

import os
import time
import threading
import datetime
from typing import Optional

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
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
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
    except Exception:
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
    if os.getenv("IGOR_CLOUD_TRAINING_ENABLED", "false").lower() not in ("1", "true", "yes"):
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
        _logging.getLogger(__name__).warning("[cloud_mode] OR balance check failed — assuming funded")
        return True
    return balance >= floor


def invalidate_cache() -> None:
    """Force next call to re-evaluate (e.g. after .env reload)."""
    global _cache_result
    with _lock:
        _cache_result = None
