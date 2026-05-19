"""Librarian tool registry — aggregates all tool schemas and dispatch."""

from __future__ import annotations

from . import (
    budget_tools,
    channel_tools,
    curation_tools,
    db_tools,
    exec_tools,
    file_tools,
    health_tools,
    igor_tools,
    manifest_tools,
    memory_tools,
    palace_tools,
    proposal_tools,
    research_tools,
    scraps_tools,
    ticket_tools,
)

SCHEMAS: list[dict] = (
    manifest_tools.SCHEMAS
    + db_tools.SCHEMAS
    + memory_tools.SCHEMAS
    + palace_tools.SCHEMAS
    + channel_tools.SCHEMAS
    + igor_tools.SCHEMAS
    + health_tools.SCHEMAS
    + research_tools.SCHEMAS
    + curation_tools.SCHEMAS
    + ticket_tools.SCHEMAS
    + exec_tools.SCHEMAS
    + file_tools.SCHEMAS
    + proposal_tools.SCHEMAS
    + budget_tools.SCHEMAS
    + scraps_tools.SCHEMAS
)


def dispatch(name: str, args: dict) -> str:
    """Route tool call to the appropriate module. Returns string result."""
    for module in (
        manifest_tools,
        db_tools,
        memory_tools,
        palace_tools,
        channel_tools,
        igor_tools,
        health_tools,
        research_tools,
        curation_tools,
        ticket_tools,
        exec_tools,
        file_tools,
        proposal_tools,
        budget_tools,
        scraps_tools,
    ):
        result = module.dispatch(name, args)
        if result is not None:
            return result
    return f"Unknown tool: {name}"
