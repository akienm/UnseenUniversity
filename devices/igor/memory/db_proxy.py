"""
db_proxy.py — Re-export shim.

Canonical implementation lives in agent_datacenter.db_proxy (T-db-proxy-igor-canonical).
This shim re-exports all public names so existing imports continue to work.
"""

from agent_datacenter.db_proxy import (  # noqa: F401
    DatabaseProxy,
    MEM_COLS,
    PGDatabaseProxy,
    _PGConnWrapper,
    _PGRowProxy,
    make_db_proxy,
    make_home_proxy,
    make_infra_proxy,
    make_local_proxy,
)
