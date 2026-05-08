"""Librarian tool registry — aggregates all tool schemas and dispatch."""

from __future__ import annotations

from . import (
    channel_tools,
    db_tools,
    health_tools,
    igor_tools,
    manifest_tools,
    memory_tools,
)

SCHEMAS: list[dict] = (
    manifest_tools.SCHEMAS
    + db_tools.SCHEMAS
    + memory_tools.SCHEMAS
    + channel_tools.SCHEMAS
    + igor_tools.SCHEMAS
    + health_tools.SCHEMAS
)


def dispatch(name: str, args: dict) -> str:
    """Route tool call to the appropriate module. Returns string result."""
    for module in (
        manifest_tools,
        db_tools,
        memory_tools,
        channel_tools,
        igor_tools,
        health_tools,
    ):
        result = module.dispatch(name, args)
        if result is not None:
            return result
    return f"Unknown tool: {name}"
