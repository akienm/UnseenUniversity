"""Canonical tool registry — Tool, ToolRegistry, ToolStats, registry singleton.

Re-exports from lab.utility_closet.registry while the full migration is in
progress. New imports should use this path; utility_closet is being retired.

T-remove-utility-closet-registry: initial migration step.
"""

from lab.utility_closet.registry import Tool, ToolRegistry, ToolStats, registry

__all__ = ["Tool", "ToolRegistry", "ToolStats", "registry"]
