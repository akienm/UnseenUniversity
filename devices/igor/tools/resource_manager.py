"""
resource_manager.py — Future home of ResourceManager (D284).

Currently a re-export shim over budget.py.  The intent (from #284) is to
expand ResourceManager to track money + compute + time + revenue once the
architecture is ready.  Until then all callers that import from budget.py
continue to work unchanged; new code should prefer importing from
resource_manager.py to signal the long-term direction.

Architecture notes (#284):
  - Home DB: global spend/revenue ledger, allocation policy, ceilings
  - Local DB: per-box resource metrics (CPU/RAM/disk/current load)
  - Revenue tracking: fits the same ledger as spend (net resource position)
  - D124 resource-auto-config results feed in here when wired
"""

from lab.utility_closet.budget import (  # noqa: F401  re-export everything callers need
    budget_status,
    check_before_call,
    check_budget_floor,
    fetch_openrouter_balance,
    get_remaining,
    get_spending_cap,
    get_spend_total,
    is_cloud_blocked,
    record_spend,
    set_spending_cap,
)

__all__ = [
    "budget_status",
    "check_before_call",
    "check_budget_floor",
    "fetch_openrouter_balance",
    "get_remaining",
    "get_spending_cap",
    "get_spend_total",
    "is_cloud_blocked",
    "record_spend",
    "set_spending_cap",
]
