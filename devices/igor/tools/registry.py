"""
registry.py — Re-export shim (T-uc-registry-move).

The canonical home for Tool, ToolStats, ToolRegistry, and the process
singleton `registry` is now `lab/utility_closet/registry.py`. This file
stays so existing imports (`from .registry import ...`,
`from wild_igor.igor.tools.registry import ...`) keep working.

New code should import from `lab.utility_closet.registry` directly.
"""

from lab.utility_closet.registry import (  # noqa: F401
    Tool,
    ToolRegistry,
    ToolStats,
    registry,
)
