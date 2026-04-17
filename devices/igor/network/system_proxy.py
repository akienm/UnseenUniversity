"""
system_proxy.py — Re-export shim.

Implementation moved to lab/utility_closet/system_proxy.py as part of the
utility closet rack architecture (T-uc-system-proxy-shelf). This shim
re-exports all public names so existing imports continue to work.

All new code should import from lab.utility_closet.system_proxy directly.
"""

# Re-export everything from the canonical location
from lab.utility_closet.system_proxy import (  # noqa: F401
    DiskInfo,
    MemoryInfo,
    ProcessInfo,
    SystemProxy,
    SystemSnapshot,
    system_proxy,
)
