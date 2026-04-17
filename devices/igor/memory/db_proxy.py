"""
db_proxy.py — Re-export shim.

Implementation moved to lab/utility_closet/db_proxy.py as part of the
utility closet rack architecture (T-uc-db-proxy-shelf). This shim
re-exports all public names so existing imports continue to work.

All new code should import from lab.utility_closet.db_proxy directly.
"""

# Re-export everything from the canonical location
from lab.utility_closet.db_proxy import (  # noqa: F401
    MEM_COLS,
    DatabaseProxy,
    PGDatabaseProxy,
    _PGConnWrapper,
    make_db_proxy,
    make_home_proxy,
    make_infra_proxy,
    make_local_proxy,
)
