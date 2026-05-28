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

from devices.igor.tools.registry import Tool, registry

from ..paths import paths as _paths

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

        db_url = _paths().home_db_url
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
        from devices.igor.tools.registry import registry
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
    import time
    from datetime import datetime

    try:
        from ..memory.cortex import Cortex

        cortex = Cortex(None)
        _t0 = time.monotonic()
        results = cortex.search(query, limit=int(limit))
        _latency_ms = (time.monotonic() - _t0) * 1000.0
        if not results:
            logger.debug(
                "memory_search: query=%r hits=0 latency_ms=%.0f", query, _latency_ms
            )
            return f"memory_search({query!r}): no results"
        _now = datetime.now()
        lines = [
            f"memory_search({query!r}): {len(results)} hit(s) ({_latency_ms:.0f}ms)"
        ]
        for m in results:
            _age_days = (_now - m.timestamp).days if m.timestamp else None
            _age_str = f" age={_age_days}d" if _age_days is not None else ""
            lines.append(f"  [{m.memory_type}] {m.id}{_age_str} — {m.narrative[:120]}")
        logger.debug(
            "memory_search: query=%r hits=%d latency_ms=%.0f",
            query,
            len(results),
            _latency_ms,
        )
        return "\n".join(lines)
    except Exception as e:
        logger.warning("memory_search: error — %s", e)
        return f"memory_search({query!r}): error — {e}"


_SYNONYM_MAP: dict[str, list[str]] = {
    "progress": ["status", "position", "reading", "list", "sessions"],
    "how far": ["progress", "position", "reading", "status"],
    "reading": ["book", "ebook", "chunk", "calibre", "learn", "absorbed"],
    "books": ["book", "ebook", "calibre", "reading", "learn", "absorbed"],
    "learn": ["reading", "book", "queue", "drain", "absorb"],
    "budget": ["balance", "spending", "cost", "openrouter"],
    "memory": ["search", "memories", "cortex", "recall", "find"],
    "search": ["memory", "find", "query", "look"],
    "file": ["read", "write", "list", "directory"],
    "habit": ["procedural", "trigger", "activation", "habit"],
    "goal": ["task", "adopt", "queue", "active"],
    "task": ["goal", "queue", "ticket", "work"],
    "metrics": ["report", "stats", "milieu", "health"],
    "machine": ["cluster", "ssh", "ollama", "router"],
    "photo": ["camera", "picture", "image", "senses"],
    "audio": ["record", "microphone", "senses"],
    "web": ["browser", "url", "webpage", "search"],
    "email": ["gmail", "inbox", "mail"],
    "calendar": ["schedule", "event", "google"],
    "experiment": ["probe", "hypothesis", "cascade", "test"],
    "decision": ["blob", "store", "decision"],
    "voice": ["generation", "graph", "word"],
    "word graph": ["wg", "predict", "spread", "generation"],
}


def find_tool(query: str, limit: int = 5, **_) -> str:
    """
    Find registered tools by keyword overlap against tool names and descriptions.
    Uses synonym expansion so conversational queries ("how far with reading?")
    match tool names ("get_reading_list", "list_reading_sessions").

    Returns the top matching tool names and their descriptions.
    Example: find_tool("search memory") → memory_search, list_unvalidated_memories, …
    """
    try:
        from devices.igor.tools.registry import registry

        def _tok(text: str) -> frozenset:
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

        def _expand(tokens: frozenset) -> frozenset:
            """Expand tokens with synonyms from _SYNONYM_MAP."""
            expanded = set(tokens)
            for tok in tokens:
                if tok in _SYNONYM_MAP:
                    expanded.update(_SYNONYM_MAP[tok])
            query_lower = " ".join(sorted(tokens))
            for phrase, syns in _SYNONYM_MAP.items():
                if " " in phrase and phrase in query_lower:
                    expanded.update(syns)
            return frozenset(expanded)

        query_tok = _tok(query)
        if not query_tok:
            return "find_tool: query too short or all stopwords"

        query_expanded = _expand(query_tok)

        scored = []
        for tool in registry._tools.values():
            text = f"{tool.name} {tool.description or ''}"
            tool_tok = _tok(text)
            if not tool_tok:
                continue
            inter = len(query_expanded & tool_tok)
            if inter == 0:
                continue
            union = len(query_expanded | tool_tok)
            score = inter / union if union else 0.0
            bonus = inter * 0.05
            score = score + bonus
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
