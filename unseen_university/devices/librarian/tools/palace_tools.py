"""Palace tools — browse, read, write, and search adc.palace nodes."""

from __future__ import annotations

import json
import os

import psycopg2
import psycopg2.extras

_PG_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

SCHEMAS = [
    {
        "name": "palace_ls",
        "description": (
            "List palace nodes under a path prefix as an indented tree. "
            "E.g. palace_ls('palace.projects') shows all project nodes. "
            "Empty prefix lists the full tree (capped at limit)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prefix": {
                    "type": "string",
                    "description": "Path prefix to list (default: '' = all)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max nodes to return (default 50)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "palace_read",
        "description": "Read a single palace node by exact path. Returns title, node_type, updated_at, tags, and full content.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Exact palace path"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "palace_write",
        "description": (
            "Upsert a palace node. Creates or updates the node at the given path. "
            "node_type defaults to 'doc'. tags is a list of strings."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Palace path (e.g. palace.projects.foo)",
                },
                "title": {"type": "string", "description": "Node title"},
                "content": {"type": "string", "description": "Node content (markdown)"},
                "node_type": {
                    "type": "string",
                    "description": "Node type: doc, decision, pointer, rollup, session, transcript (default: doc)",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tag list for GIN-indexed filtering (default: [])",
                },
            },
            "required": ["path", "title", "content"],
        },
    },
    {
        "name": "palace_search",
        "description": (
            "Full-text search across palace node titles and content. "
            "Optionally filter by tag. Returns path, title, and a content snippet."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Require all listed tags (optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 10)",
                },
            },
            "required": ["query"],
        },
    },
]


def _q(sql: str, params=(), pg_url: str = _PG_URL) -> list[dict]:
    with psycopg2.connect(pg_url) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def _exec(sql: str, params=(), pg_url: str = _PG_URL) -> int:
    with psycopg2.connect(pg_url) as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.rowcount


def palace_ls(prefix: str = "", limit: int = 50, pg_url: str = _PG_URL) -> str:
    if prefix:
        rows = _q(
            "SELECT path, title, node_type, updated_at::date AS date "
            "FROM adc.palace WHERE path LIKE %s ORDER BY path LIMIT %s",
            (f"{prefix}%", limit),
            pg_url,
        )
    else:
        rows = _q(
            "SELECT path, title, node_type, updated_at::date AS date "
            "FROM adc.palace ORDER BY path LIMIT %s",
            (limit,),
            pg_url,
        )
    if not rows:
        return f"No nodes found under prefix '{prefix}'."

    # Indented tree: count dots to determine depth
    lines = [f"{len(rows)} node(s):"]
    for r in rows:
        depth = r["path"].count(".") - 1  # palace.x = 1 dot = depth 0
        indent = "  " * max(0, depth)
        name = r["path"].split(".")[-1]
        lines.append(
            f"{indent}{name}  [{r['node_type']}]  {r['date']}  — {r['title'][:60]}"
        )
    return "\n".join(lines)


def palace_read(path: str, pg_url: str = _PG_URL) -> str:
    rows = _q(
        "SELECT path, title, content, node_type, updated_at, metadata "
        "FROM adc.palace WHERE path = %s",
        (path,),
        pg_url,
    )
    if not rows:
        return f"No node found at path '{path}'."
    r = rows[0]
    meta = r["metadata"] or {}
    tags = meta.get("tags", [])
    return (
        f"path:      {r['path']}\n"
        f"title:     {r['title']}\n"
        f"node_type: {r['node_type']}\n"
        f"updated:   {r['updated_at']}\n"
        f"tags:      {tags}\n"
        f"---\n{r['content']}"
    )


def palace_write(
    path: str,
    title: str,
    content: str,
    node_type: str = "doc",
    tags: list | None = None,
    pg_url: str = _PG_URL,
) -> str:
    metadata = psycopg2.extras.Json({"tags": tags or []})
    with psycopg2.connect(pg_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO adc.palace (path, title, content, node_type, updated_at, metadata)
                   VALUES (%s, %s, %s, %s, now(), %s)
                   ON CONFLICT (path) DO UPDATE
                       SET title=EXCLUDED.title, content=EXCLUDED.content,
                           node_type=EXCLUDED.node_type, updated_at=EXCLUDED.updated_at,
                           metadata=EXCLUDED.metadata
                   RETURNING updated_at""",
                (path, title, content, node_type, metadata),
            )
            row = cur.fetchone()
    return f"Written: {path} (updated_at={row[0]})"


def palace_search(
    query: str, tags: list | None = None, limit: int = 10, pg_url: str = _PG_URL
) -> str:
    params: list = []
    where_parts = [
        "to_tsvector('english', coalesce(content,'') || ' ' || coalesce(title,'')) "
        "@@ plainto_tsquery('english', %s)"
    ]
    params.append(query)
    if tags:
        for tag in tags:
            where_parts.append("metadata @> %s::jsonb")
            params.append(json.dumps({"tags": [tag]}))
    params.append(limit)
    rows = _q(
        f"SELECT path, title, left(content, 200) AS snippet "
        f"FROM adc.palace WHERE {' AND '.join(where_parts)} "
        f"ORDER BY updated_at DESC LIMIT %s",
        params,
        pg_url,
    )
    if not rows:
        return f"No results for query '{query}'."
    lines = [f"{len(rows)} result(s) for '{query}':"]
    for r in rows:
        lines.append(f"\n{r['path']}\n  {r['title']}\n  {r['snippet'][:150]}…")
    return "\n".join(lines)


def dispatch(name: str, args: dict, pg_url: str = _PG_URL) -> str | None:
    if name == "palace_ls":
        return palace_ls(args.get("prefix", ""), args.get("limit", 50), pg_url)
    if name == "palace_read":
        return palace_read(args["path"], pg_url)
    if name == "palace_write":
        return palace_write(
            args["path"],
            args["title"],
            args["content"],
            args.get("node_type", "doc"),
            args.get("tags"),
            pg_url,
        )
    if name == "palace_search":
        return palace_search(
            args["query"], args.get("tags"), args.get("limit", 10), pg_url
        )
    return None
