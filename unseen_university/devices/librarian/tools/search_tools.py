"""search_tools.py — unified fulltext search across all knowledge sources.

Zero inference tokens in the hot path. Returns a compact ranked list;
callers read specific items only if they want depth.

Sources:
  palace   — adc.palace nodes (concepts, decisions, summaries, day rollups)
  memories — clan.memories (all memory types)
  tickets  — filesystem ticket store (D-build-queue-filesystem-first-2026-06-19)
  files    — repo files via ripgrep (no DB)
  all      — palace + memories + tickets + files (default)
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

_PG_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)
_REPO_ROOT = Path(__file__).resolve().parents[5]
_SNIPPET_LEN = 90
_VALID_SOURCES = frozenset({"all", "palace", "memories", "tickets", "files"})


def _conn():
    import psycopg2
    return psycopg2.connect(_PG_URL)


def _snip(text: str) -> str:
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= _SNIPPET_LEN:
        return text
    return text[:_SNIPPET_LEN].rstrip() + "…"


def _search_palace(query: str, limit: int) -> list[dict]:
    hits: list[dict] = []
    try:
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT path, title, content,
                       ts_rank(
                           to_tsvector('english', coalesce(content,'') || ' ' || coalesce(title,'')),
                           plainto_tsquery('english', %s)
                       ) AS rank
                FROM adc.palace
                WHERE to_tsvector('english', coalesce(content,'') || ' ' || coalesce(title,''))
                      @@ plainto_tsquery('english', %s)
                ORDER BY rank DESC
                LIMIT %s
                """,
                (query, query, limit),
            )
            for path, title, content, rank in cur.fetchall():
                hits.append(
                    {
                        "source": "palace",
                        "key": path,
                        "rank": float(rank),
                        "snippet": _snip(content or title or ""),
                    }
                )
        conn.close()
    except Exception:
        pass
    return hits


def _search_memories(query: str, limit: int) -> list[dict]:
    """Fulltext over the clan.memories knowledge-store (NOT ticket-state).

    Ticket-state moved to the filesystem store (see ``_search_tickets``); this
    path stays on Postgres because it searches the general memory corpus. Rows
    with ``kind='ticket'`` are EXCLUDED here — tickets are filesystem-only now
    (D-build-queue-filesystem-first-2026-06-19), so they must surface solely
    from ``_search_tickets``, never from the DB, even while vestigial ticket
    rows linger in clan.memories until #5 (T-ticket-pg-drop) removes them.
    """
    hits: list[dict] = []
    try:
        conn = _conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT name, narrative,
                       ts_rank(
                           to_tsvector('english', coalesce(narrative,'')),
                           plainto_tsquery('english', %s)
                       ) AS rank
                FROM clan.memories
                WHERE to_tsvector('english', coalesce(narrative,''))
                      @@ plainto_tsquery('english', %s)
                  AND metadata->>'kind' IS DISTINCT FROM 'ticket'
                ORDER BY rank DESC
                LIMIT %s
                """,
                (query, query, limit),
            )
            for name, narrative, rank in cur.fetchall():
                hits.append(
                    {
                        "source": "memory",
                        "key": name or "?",
                        "rank": float(rank),
                        "snippet": _snip(narrative or ""),
                    }
                )
        conn.close()
    except Exception:
        pass
    return hits


def _search_tickets(query: str, limit: int) -> list[dict]:
    """Ticket-state search over the filesystem ticket store (no Postgres).

    Filesystem-first (D-build-queue-filesystem-first-2026-06-19): ticket state
    lives in ``devlab/runtime/memory/tickets/`` (+ ``closed/``), not clan.memories.
    Postgres has no fulltext index out here, so this is a case-insensitive
    term-presence match over id/title/description/tags — the honest filesystem
    equivalent of the old ts_rank ticket query. Rank = fraction of query terms
    present (0..1), scaled modestly so tickets interleave with ts_rank hits rather
    than always dominating. Reads are lock-free (atomic files are always valid).
    """
    hits: list[dict] = []
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return hits
    try:
        from unseen_university import ticket_store

        for body in ticket_store.list(include_closed=True):
            tags = body.get("tags") or []
            blob = " ".join([
                str(body.get("id") or ""),
                str(body.get("title") or ""),
                str(body.get("description") or ""),
                " ".join(str(t) for t in tags),
            ]).lower()
            matched = sum(1 for t in terms if t in blob)
            if not matched:
                continue
            hits.append(
                {
                    "source": "ticket",
                    "key": body.get("id") or "?",
                    "rank": 0.5 * (matched / len(terms)),
                    "snippet": _snip(body.get("title") or body.get("description") or ""),
                }
            )
    except Exception:
        return []
    hits.sort(key=lambda h: h["rank"], reverse=True)
    return hits[:limit]


def _search_files(query: str, limit: int) -> list[dict]:
    hits: list[dict] = []
    try:
        proc = subprocess.run(
            [
                "rg",
                "--no-heading",
                "--line-number",
                "--max-count=1",
                "--max-depth=6",
                "-g", "*.py",
                "-g", "*.md",
                "-g", "*.yaml",
                "-g", "!__pycache__",
                "-g", "!.venv",
                "-g", "!test_env",
                query,
                str(_REPO_ROOT),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in proc.stdout.split("\n"):
            if not line.strip():
                continue
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            fpath, lineno, content = parts[0], parts[1], parts[2]
            try:
                rel = str(Path(fpath).relative_to(_REPO_ROOT))
            except ValueError:
                rel = fpath
            hits.append(
                {
                    "source": "file",
                    "key": f"{rel}:{lineno}",
                    "rank": 0.5,
                    "snippet": _snip(content),
                }
            )
            if len(hits) >= limit:
                break
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return hits


def search(query: str, source: str = "all", limit: int = 10) -> str:
    """Unified search across all indexed knowledge.

    Returns a compact ranked list — ~10-20 tokens per hit. Callers use
    Read / memory_get / palace_read for depth on specific items.
    """
    if not query or not query.strip():
        return "query required"

    source = source.strip().lower()
    if source not in _VALID_SOURCES:
        source = "all"

    per_src = max(limit, 10)
    hits: list[dict] = []

    if source in ("all", "palace"):
        hits.extend(_search_palace(query, per_src))
    if source in ("all", "memories"):
        hits.extend(_search_memories(query, per_src))
    if source in ("all", "tickets"):
        hits.extend(_search_tickets(query, per_src))
    if source in ("all", "files"):
        hits.extend(_search_files(query, per_src))

    if not hits:
        return f"No results for {query!r}"

    hits.sort(key=lambda h: h["rank"], reverse=True)
    hits = hits[:limit]

    lines: list[str] = []
    for h in hits:
        snip = h.get("snippet", "")
        if snip:
            lines.append(f'{h["source"]}: {h["key"]} ({h["rank"]:.2f}) — "{snip}"')
        else:
            lines.append(f'{h["source"]}: {h["key"]} ({h["rank"]:.2f})')
    return "\n".join(lines)


SCHEMAS: list[dict] = [
    {
        "name": "search",
        "description": (
            "Search all indexed knowledge — palace nodes, memories, tickets, repo files. "
            "Returns a compact ranked list (~10-20 tokens per hit). "
            "Zero inference in the hot path. Use to answer 'what do I know about X?' "
            "before spending tokens on deeper reads."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (plain English, 1-10 words)",
                },
                "source": {
                    "type": "string",
                    "enum": ["all", "palace", "memories", "tickets", "files"],
                    "description": "Narrow to a specific source (default: all)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max hits to return (default 10, max 50)",
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "required": ["query"],
        },
    }
]


def dispatch(name: str, args: dict):
    if name == "search":
        return search(
            query=args.get("query", ""),
            source=args.get("source", "all"),
            limit=int(args.get("limit", 10)),
        )
    return None
