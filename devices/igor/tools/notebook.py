"""
The Master's Notebook — per-user knowledge base (#153).

Each user gets their own SQLite database at:
  ~/.TheIgors/igor_wild_0001/chats/<slug>/notebook.db

Igor saves reference material (text, URLs, files) into the user's notebook
and searches it semantically. Completely separate from Igor's own memory graph.

Public API (also registered as LLM tools):
  save_entry(user_slug, title, content, source, tags) → str
  search_notebook(user_slug, query, limit)            → str
  list_notebook(user_slug)                            → str
  remove_entry(user_slug, id_or_title)                → str
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

from .registry import Tool, registry

# ── Paths ──────────────────────────────────────────────────────────────────────

_INSTANCE_DIR  = Path.home() / ".TheIgors" / "igor_wild_0001"
_CHATS_DIR     = _INSTANCE_DIR / "chats"
_CHUNK_SIZE    = 1500
_CHUNK_OVERLAP = 150

# ── DB ─────────────────────────────────────────────────────────────────────────

def _db_path(user_slug: str) -> Path:
    return _CHATS_DIR / user_slug / "notebook.db"


def _get_db(user_slug: str) -> sqlite3.Connection:
    path = _db_path(user_slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS entries (
            id           TEXT PRIMARY KEY,
            title        TEXT NOT NULL,
            source       TEXT DEFAULT '',
            content      TEXT NOT NULL,
            embedding    TEXT,
            tags         TEXT DEFAULT '',
            ingested_at  TEXT NOT NULL,
            chunk_index  INTEGER DEFAULT 0,
            total_chunks INTEGER DEFAULT 1
        )
    """)
    con.commit()
    return con

# ── Chunking ───────────────────────────────────────────────────────────────────

def _chunk_text(text: str) -> list[str]:
    if len(text) <= _CHUNK_SIZE:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        end = start + _CHUNK_SIZE
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = end - _CHUNK_OVERLAP
    return chunks


def _entry_id(user_slug: str, title: str, chunk_index: int) -> str:
    raw = f"{user_slug}:{title}:{chunk_index}"
    return "NB_" + hashlib.sha256(raw.encode()).hexdigest()[:12]

# ── Embedding helpers ──────────────────────────────────────────────────────────

def _embed(text: str) -> Optional[list[float]]:
    try:
        from ..cognition.embedder import embed
        return embed(text)
    except Exception:
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    try:
        from ..cognition.embedder import cosine_similarity
        return cosine_similarity(a, b)
    except Exception:
        dot = sum(x * y for x, y in zip(a, b))
        na  = sum(x * x for x in a) ** 0.5
        nb  = sum(x * x for x in b) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

# ── Core operations ────────────────────────────────────────────────────────────

def save_entry(
    user_slug: str,
    title: str,
    content: str,
    source: str = "paste",
    tags: str = "",
) -> str:
    """Chunk, embed, and store content in the user's notebook."""
    chunks = _chunk_text(content)
    total  = len(chunks)
    now    = datetime.now().isoformat(timespec="seconds")
    con    = _get_db(user_slug)
    saved  = 0
    try:
        for i, chunk in enumerate(chunks):
            eid       = _entry_id(user_slug, title, i)
            embedding = _embed(chunk)
            con.execute(
                """INSERT OR REPLACE INTO entries
                   (id, title, source, content, embedding, tags, ingested_at, chunk_index, total_chunks)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    eid, title, source, chunk,
                    json.dumps(embedding) if embedding else None,
                    tags, now, i, total,
                ),
            )
            saved += 1
        con.commit()
    finally:
        con.close()
    chunks_str = f"{saved} chunk{'s' if saved != 1 else ''}"
    return (
        f"Saved to your notebook: **{title}**\n"
        f"  {chunks_str} · {len(content):,} chars · source: {source}"
        + (f"\n  Tags: {tags}" if tags else "")
    )


def search_notebook(user_slug: str, query: str, limit: int = 5) -> str:
    """Semantic search over the user's notebook."""
    db = _db_path(user_slug)
    if not db.exists():
        return "Your notebook is empty — nothing saved yet."
    con = _get_db(user_slug)
    try:
        rows = con.execute(
            "SELECT id, title, source, content, embedding FROM entries"
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return "Your notebook is empty — nothing saved yet."

    query_vec = _embed(query)
    scored = []
    for row in rows:
        if query_vec and row["embedding"]:
            try:
                sim = _cosine(query_vec, json.loads(row["embedding"]))
            except Exception:
                sim = 0.0
        else:
            # Keyword fallback when Ollama unavailable
            words = set(query.lower().split())
            hits  = sum(w in row["content"].lower() for w in words)
            sim   = hits / max(1, len(words)) * 0.5
        scored.append((sim, row))

    scored.sort(key=lambda x: x[0], reverse=True)
    top = [x for x in scored[:limit] if x[0] >= 0.1]
    if not top:
        return f"Nothing relevant found in your notebook for: {query!r}"

    lines = [f"From your notebook — '{query}':"]
    seen = set()
    for sim, row in top:
        title = row["title"]
        cont  = row["content"].replace("\n", " ").strip()
        marker = " (cont.)" if title in seen else ""
        seen.add(title)
        lines.append(
            f"\n  **{title}**{marker}  (relevance: {sim:.2f})\n"
            f"  {cont[:200]}{'…' if len(cont) > 200 else ''}"
        )
    return "\n".join(lines)


def list_notebook(user_slug: str) -> str:
    """List all entries in the user's notebook, deduplicated by title."""
    db = _db_path(user_slug)
    if not db.exists():
        return "Your notebook is empty — nothing saved yet."
    con = _get_db(user_slug)
    try:
        rows = con.execute(
            """SELECT title, source, tags, ingested_at, SUM(total_chunks) as chunks
               FROM entries GROUP BY title ORDER BY ingested_at DESC"""
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return "Your notebook is empty — nothing saved yet."
    lines = [f"Your notebook ({len(rows)} item{'s' if len(rows) != 1 else ''}):"]
    for r in rows:
        date   = r["ingested_at"][:10]
        chunks = f" · {r['chunks']} chunks" if r["chunks"] > 1 else ""
        tags   = f" · [{r['tags']}]" if r["tags"] else ""
        lines.append(f"  {date}  **{r['title']}**{chunks}{tags}")
        if r["source"] and r["source"] not in ("paste", ""):
            lines.append(f"           {r['source'][:80]}")
    return "\n".join(lines)


def remove_entry(user_slug: str, id_or_title: str) -> str:
    """Remove entries by ID prefix or exact title."""
    db = _db_path(user_slug)
    if not db.exists():
        return "Your notebook is empty."
    con = _get_db(user_slug)
    try:
        rows = con.execute(
            "SELECT id, title FROM entries WHERE id LIKE ?",
            (id_or_title + "%",)
        ).fetchall()
        if not rows:
            rows = con.execute(
                "SELECT id, title FROM entries WHERE title = ?",
                (id_or_title,)
            ).fetchall()
        if not rows:
            return f"Nothing found in your notebook matching '{id_or_title}'."
        titles = set(r["title"] for r in rows)
        for r in rows:
            con.execute("DELETE FROM entries WHERE id = ?", (r["id"],))
        con.commit()
        return f"Removed {len(rows)} chunk{'s' if len(rows) != 1 else ''}: {', '.join(titles)}"
    finally:
        con.close()

# ── Tool wrappers ──────────────────────────────────────────────────────────────

def _tool_save(user_slug: str, title: str, content: str,
               source: str = "paste", tags: str = "", **_) -> str:
    return save_entry(user_slug, title, content, source, tags)

def _tool_search(user_slug: str, query: str, limit: int = 5, **_) -> str:
    return search_notebook(user_slug, query, limit)

def _tool_list(user_slug: str, **_) -> str:
    return list_notebook(user_slug)

def _tool_remove(user_slug: str, id_or_title: str, **_) -> str:
    return remove_entry(user_slug, id_or_title)

# ── Register tools ─────────────────────────────────────────────────────────────

registry.register(Tool(
    name="notebook_save",
    description=(
        "Save content to the user's personal notebook for future reference. "
        "Use when the user says 'remember this', 'save this', 'add to my notebook', "
        "'keep a note of this', or similar save-intent phrases. "
        "Chunks and embeds the content for later semantic search. "
        "The notebook belongs to the user, not Igor — use the user_slug from the "
        "TALKING WITH context block."
    ),
    parameters={
        "type": "object",
        "properties": {
            "user_slug": {"type": "string",
                          "description": "User slug from TALKING WITH context (e.g. 'akien')"},
            "title":     {"type": "string",
                          "description": "Short descriptive title for this entry"},
            "content":   {"type": "string",
                          "description": "Full content to save verbatim"},
            "source":    {"type": "string",
                          "description": "Where this came from: 'paste', a URL, or file path"},
            "tags":      {"type": "string",
                          "description": "Comma-separated tags (optional)"},
        },
        "required": ["user_slug", "title", "content"],
    },
    fn=_tool_save,
))

registry.register(Tool(
    name="notebook_search",
    description=(
        "Search the user's personal notebook semantically. "
        "Use when the user asks about something they may have previously saved, "
        "or when relevant background context might be in their notebook."
    ),
    parameters={
        "type": "object",
        "properties": {
            "user_slug": {"type": "string", "description": "User slug"},
            "query":     {"type": "string", "description": "What to search for"},
            "limit":     {"type": "integer", "description": "Max results (default 5)"},
        },
        "required": ["user_slug", "query"],
    },
    fn=_tool_search,
))

registry.register(Tool(
    name="notebook_list",
    description="List all items in the user's personal notebook.",
    parameters={
        "type": "object",
        "properties": {
            "user_slug": {"type": "string", "description": "User slug"},
        },
        "required": ["user_slug"],
    },
    fn=_tool_list,
))

registry.register(Tool(
    name="notebook_remove",
    description="Remove an item from the user's notebook by entry ID prefix or exact title.",
    parameters={
        "type": "object",
        "properties": {
            "user_slug":    {"type": "string", "description": "User slug"},
            "id_or_title":  {"type": "string",
                             "description": "Entry ID prefix (e.g. 'NB_abc123') or exact title"},
        },
        "required": ["user_slug", "id_or_title"],
    },
    fn=_tool_remove,
))
