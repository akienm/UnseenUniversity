"""
system_proxy.py — Re-export shim.

Implementation lives at lab/utility_closet/system_proxy.py (T-uc-system-proxy-shelf).
This shim re-exports all public names so existing imports continue to work.

New code should import from lab.utility_closet.system_proxy directly.
"""
from lab.utility_closet.system_proxy import (  # noqa: F401
    DiskInfo,
    MemoryInfo,
    ProcessInfo,
    SystemProxy,
    SystemSnapshot,
    system_proxy,
)
