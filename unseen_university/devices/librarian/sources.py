"""Curated source index for Librarian external doc fetch.

Maps topic keyword patterns to authoritative documentation URLs.
Used by ResearchEngine._research_unified when depth >= 0.6.
"""

from __future__ import annotations

SOURCES: list[dict] = [
    {
        "patterns": [
            "anthropic",
            "claude api",
            "anthropic api",
            "messages api",
            "claude model",
        ],
        "url": "https://docs.anthropic.com/en/docs/overview",
        "description": "Anthropic API docs",
    },
    {
        "patterns": [
            "psycopg2",
            "psycopg",
            "postgres",
            "postgresql",
            "pg_",
            "pg connect",
        ],
        "url": "https://www.psycopg.org/docs/usage.html",
        "description": "psycopg2 usage docs",
    },
    {
        "patterns": [
            "python",
            "stdlib",
            "standard library",
            "builtin",
            "built-in",
            "asyncio",
            "pathlib",
            "dataclass",
            "typing",
            "collections",
            "itertools",
        ],
        "url": "https://docs.python.org/3/library/index.html",
        "description": "Python stdlib docs",
    },
    {
        "patterns": ["imap", "imap idle", "email protocol", "smtp", "mime"],
        "url": "https://docs.python.org/3/library/imaplib.html",
        "description": "Python imaplib docs",
    },
    {
        "patterns": ["sqlite", "sqlite3"],
        "url": "https://docs.python.org/3/library/sqlite3.html",
        "description": "Python sqlite3 docs",
    },
    {
        "patterns": [
            "starlette",
            "fastapi",
            "uvicorn",
            "asgi",
            "http server",
            "web framework",
        ],
        "url": "https://www.starlette.io/",
        "description": "Starlette docs",
    },
    {
        "patterns": ["mcp", "model context protocol", "mcp server", "mcp tool"],
        "url": "https://modelcontextprotocol.io/docs/concepts/tools",
        "description": "MCP tools docs",
    },
]


def match_sources(query: str, max_sources: int = 2) -> list[dict]:
    """Return curated sources whose patterns overlap with the query.

    Returns at most max_sources entries, in SOURCES order (priority order).
    """
    query_lower = query.lower()
    matched = []
    for source in SOURCES:
        if any(p in query_lower for p in source["patterns"]):
            matched.append(source)
        if len(matched) >= max_sources:
            break
    return matched
