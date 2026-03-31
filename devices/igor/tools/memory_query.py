"""
Memory and tool discovery queries — D272.

Functions for Igor to introspect his own tools, capabilities, and memory structure.
"""

import os
import json

from .registry import Tool, registry


def _get_cortex():
    """Get the cortex singleton from the running Igor instance."""
    from ..memory.cortex import Cortex

    return Cortex(None)


def list_facia_memories(**_) -> str:
    """
    List all facia memories (entry points into named structures like trees or tool groups).

    Facia memories are indexed by ID pattern INTERP_FACIA_* and have metadata.facia=true.
    Returns: id, associated tool_name, brief description.
    """
    try:
        import psycopg2

        db_url = os.environ.get(
            "IGOR_HOME_DB_URL",
            "postgresql://igor:choose_a_password@127.0.0.1/igor_wild_0001",
        )
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("""
            SELECT id, metadata, LEFT(narrative, 80) as preview
            FROM memories
            WHERE id LIKE 'INTERP_FACIA_%'
            ORDER BY activation_count DESC
        """)
        rows = cur.fetchall()
        conn.close()

        if not rows:
            return "No facia memories found."

        lines = [f"Found {len(rows)} facia memory/memories:"]
        for id_, metadata_json, preview in rows:
            try:
                meta = json.loads(metadata_json) if metadata_json else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}
            tool_name = meta.get("tool_name", "—")
            lines.append(f"\n  [{id_}]  tool: {tool_name}\n" f"    Preview: {preview}")
        return "\n".join(lines)

    except Exception as e:
        return f"[ERROR listing facia memories] {e}"


def get_tool_registry_report(filter_text: str = "", **_) -> str:
    """
    List all registered tools in Igor's tool registry.

    filter_text: optional filter by name (substring match, case-insensitive)
    Returns: tool names and descriptions, optionally filtered.
    """
    try:
        from .registry import registry
        from .. import tools as _tools_pkg  # noqa — ensures all tools are registered

        tools = sorted(registry._tools.values(), key=lambda x: x.name)
        if filter_text:
            filter_text = filter_text.lower()
            tools = [t for t in tools if filter_text in t.name.lower()]

        if not tools:
            return (
                f"No tools found matching '{filter_text}'."
                if filter_text
                else "No tools registered."
            )

        lines = [f"Registered tools ({len(tools)} total):"]
        for tool in tools:
            desc = (
                tool.description.split("\n")[0]
                if tool.description
                else "(no description)"
            )
            lines.append(f"\n  {tool.name}\n    {desc[:100]}")

        return "\n".join(lines)

    except Exception as e:
        return f"[ERROR listing tools] {e}"


# ── Registration ──────────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="list_facia_memories",
        description=(
            "List all facia memories (entry points into named structures). "
            "Facia memories have ID pattern INTERP_FACIA_* and index tools/capabilities."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
        fn=list_facia_memories,
    )
)

registry.register(
    Tool(
        name="get_tool_registry_report",
        description=(
            "List all registered tools available to Igor. "
            "Optional filter by name substring."
        ),
        parameters={
            "type": "object",
            "properties": {
                "filter_text": {
                    "type": "string",
                    "description": "Optional substring to filter tool names (case-insensitive)",
                },
            },
            "required": [],
        },
        fn=get_tool_registry_report,
    )
)
