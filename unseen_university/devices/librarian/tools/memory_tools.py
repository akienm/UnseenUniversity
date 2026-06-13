"""Memory tools — search, get, and list Igor's memory palace."""

from __future__ import annotations

import json
import os

import psycopg2
import psycopg2.extras

_PG_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

SCHEMAS = [
    {
        "name": "memory_search",
        "description": (
            "Full-text search over Igor's memory graph. "
            "Returns top matches by narrative keyword overlap. "
            "Optionally filter by memory_type."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms"},
                "limit": {"type": "integer", "description": "Max results (default 10)"},
                "memory_type": {
                    "type": "string",
                    "description": (
                        "Filter by type: FACTUAL|INTERPRETIVE|PROCEDURAL|EPISODIC|"
                        "EXPERIENTIAL|IDENTITY|ROOT|CORE_PATTERN (optional)"
                    ),
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory_get",
        "description": "Get a single memory node by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "string", "description": "Memory node ID"},
            },
            "required": ["memory_id"],
        },
    },
    {
        "name": "memory_list_by_type",
        "description": "List recent memories of a specific type, ordered by activation_count.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "memory_type": {
                    "type": "string",
                    "description": "FACTUAL|INTERPRETIVE|PROCEDURAL|EPISODIC|etc.",
                },
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
            "required": ["memory_type"],
        },
    },
]


def _q(sql: str, params=(), pg_url: str = _PG_URL) -> list[dict]:
    with psycopg2.connect(pg_url) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def memory_search(
    query: str, limit: int = 10, memory_type: str | None = None, pg_url: str = _PG_URL
) -> str:
    query = query.strip()
    if not query:
        return "No query terms provided."
    params: list = [query]
    type_clause = ""
    if memory_type:
        type_clause = " AND memory_type = %s"
        params.append(memory_type.upper())
    params.append(limit)
    rows = _q(
        "SELECT id, memory_type, narrative, activation_count, metadata "
        "FROM memories "
        "WHERE to_tsvector('english', narrative) @@ plainto_tsquery('english', %s)"
        f"{type_clause} "
        "ORDER BY activation_count DESC LIMIT %s",
        params,
        pg_url,
    )
    if not rows:
        return f"No memories found for: {query}"
    lines = [f"Found {len(rows)} memories for '{query}':\n"]
    for r in rows:
        snippet = (r["narrative"] or "")[:120].replace("\n", " ")
        lines.append(f"  [{r['memory_type']}] {r['id']}\n    {snippet}")
    return "\n".join(lines)


def memory_get(memory_id: str, pg_url: str = _PG_URL) -> str:
    rows = _q(
        "SELECT id, memory_type, narrative, activation_count, metadata, "
        "parent_id, children_ids, link_ids, timestamp, last_accessed "
        "FROM memories WHERE id = %s",
        (memory_id,),
        pg_url,
    )
    if not rows:
        return f"Memory not found: {memory_id}"
    r = rows[0]
    meta = r.get("metadata") or {}
    return (
        f"ID: {r['id']}\n"
        f"Type: {r['memory_type']}\n"
        f"Activations: {r['activation_count']}\n"
        f"Last accessed: {r['last_accessed']}\n"
        f"Parent: {r['parent_id']}\n"
        f"Children: {r['children_ids']}\n"
        f"Links: {r['link_ids']}\n"
        f"Metadata: {json.dumps(meta, indent=2)}\n"
        f"Narrative:\n{r['narrative']}"
    )


def memory_list_by_type(
    memory_type: str, limit: int = 20, pg_url: str = _PG_URL
) -> str:
    rows = _q(
        "SELECT id, narrative, activation_count FROM memories "
        "WHERE memory_type = %s ORDER BY activation_count DESC LIMIT %s",
        (memory_type.upper(), limit),
        pg_url,
    )
    if not rows:
        return f"No memories of type {memory_type}"
    lines = [f"{len(rows)} {memory_type} memories:\n"]
    for r in rows:
        snippet = (r["narrative"] or "")[:100].replace("\n", " ")
        lines.append(f"  [{r['activation_count']}] {r['id']}\n    {snippet}")
    return "\n".join(lines)


def dispatch(name: str, args: dict, pg_url: str = _PG_URL) -> str | None:
    if name == "memory_search":
        return memory_search(
            args["query"], args.get("limit", 10), args.get("memory_type"), pg_url
        )
    if name == "memory_get":
        return memory_get(args["memory_id"], pg_url)
    if name == "memory_list_by_type":
        return memory_list_by_type(args["memory_type"], args.get("limit", 20), pg_url)
    return None
