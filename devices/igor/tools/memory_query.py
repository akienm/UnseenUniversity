"""
Memory and tool discovery queries — D272.

Functions for Igor to introspect his own tools, capabilities, and memory structure.
Includes memory_search (sync in-turn memory lookup) and find_tool (fuzzy tool name
resolution by keyword overlap against names+descriptions).
"""

import logging
import os
import json
import re

from .registry import Tool, registry

logger = logging.getLogger(__name__)


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
            "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
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


def memory_search(query: str, limit: int = 5, **_) -> str:
    """
    Search Igor's memory store by keyword overlap. Returns top matches as a
    readable string. Synchronous — result available immediately in this turn.

    Use this when you need to look up what you know about a topic without
    waiting for a deferred task. For large background lookups use
    DEFERRED_TASK|memory_search|<query> instead.
    """
    try:
        from ..memory.cortex import Cortex

        cortex = Cortex(None)
        results = cortex.search(query, limit=int(limit))
        if not results:
            return f"memory_search({query!r}): no results"
        lines = [f"memory_search({query!r}): {len(results)} hit(s)"]
        for m in results:
            lines.append(f"  [{m.memory_type}] {m.id} — {m.narrative[:120]}")
        logger.debug("memory_search: query=%r hits=%d", query, len(results))
        return "\n".join(lines)
    except Exception as e:
        logger.warning("memory_search: error — %s", e)
        return f"memory_search({query!r}): error — {e}"


def find_tool(query: str, limit: int = 5, **_) -> str:
    """
    Find registered tools by keyword overlap against tool names and descriptions.
    Useful when you know roughly what a tool does but not its exact name.

    Returns the top matching tool names and their descriptions.
    Example: find_tool("search memory") → memory_search, list_unvalidated_memories, …
    """
    try:
        from .registry import registry

        def _tok(text: str) -> frozenset:
            # Split on underscores/hyphens first so tool_name → ["tool", "name"]
            text = re.sub(r"[_\-]", " ", text.lower())
            words = re.findall(r"[a-z][a-z0-9]*", text)
            stopwords = {
                "the",
                "a",
                "an",
                "to",
                "for",
                "of",
                "in",
                "is",
                "it",
                "and",
                "or",
                "by",
                "be",
                "as",
                "at",
                "do",
                "if",
                "on",
            }
            return frozenset(w for w in words if w not in stopwords and len(w) >= 3)

        query_tok = _tok(query)
        if not query_tok:
            return "find_tool: query too short or all stopwords"

        scored = []
        for tool in registry._tools.values():
            text = f"{tool.name} {tool.description or ''}"
            tool_tok = _tok(text)
            if not tool_tok:
                continue
            inter = len(query_tok & tool_tok)
            union = len(query_tok | tool_tok)
            score = inter / union if union else 0.0
            if score > 0:
                scored.append((score, tool.name, tool.description or ""))

        scored.sort(reverse=True)
        top = scored[: int(limit)]
        if not top:
            return f"find_tool({query!r}): no matching tools"

        lines = [f"find_tool({query!r}): {len(top)} match(es)"]
        for score, name, desc in top:
            lines.append(
                f"  {name} (score={score:.2f}) — {desc.split(chr(10))[0][:80]}"
            )
        logger.debug("find_tool: query=%r top=%s", query, [n for _, n, _ in top])
        return "\n".join(lines)
    except Exception as e:
        logger.warning("find_tool: error — %s", e)
        return f"find_tool({query!r}): error — {e}"


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

registry.register(
    Tool(
        name="memory_search",
        description=(
            "Search Igor's memory store by keyword overlap. Returns top matches synchronously "
            "in the current turn. Use for quick lookups. For large background lookups use "
            "DEFERRED_TASK|memory_search|<query> instead."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search terms to match against memory narratives",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 5)",
                },
            },
            "required": ["query"],
        },
        fn=memory_search,
    )
)

registry.register(
    Tool(
        name="find_tool",
        description=(
            "Find registered tools by keyword overlap against tool names and descriptions. "
            "Use when you know roughly what a tool does but not its exact name. "
            "Example: find_tool('search memory') resolves to memory_search."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keywords describing what the tool does",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 5)",
                },
            },
            "required": ["query"],
        },
        fn=find_tool,
    )
)
