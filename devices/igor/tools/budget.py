"""
budget.py — Re-export shim (T-uc-budget-shelf inverted 2026-04-19).

The canonical implementation lives at lab/utility_closet/budget.py.
Existing `from ..tools.budget import ...` imports keep working via this
shim. New code should import from `lab.utility_closet.budget` directly.
"""

from lab.utility_closet.budget import *  # noqa: F401, F403

# Explicit re-export of names legacy callers / tests sometimes patch on
# this module. Anything else is covered by the star-import above.
from lab.utility_closet.budget import (  # noqa: F401
    CRITICAL_USD,
    DEFAULT_SPENDING_CAP_USD,
    Tool,
    WARN_FRACTION,
    _BALANCE_CACHE_TTL_SEC,
    _OR_CREDITS_URL,
    _balance_cache,
    _db_proxy,
    budget_status,
    check_before_call,
    check_budget_floor,
    fetch_openrouter_balance,
    get_balance_trajectory,
    get_remaining,
    get_spend_total,
    get_spending_cap,
    is_cloud_blocked,
    log_error,
    query_costs_log,
    record_spend,
    registry,
    set_spending_cap,
)
