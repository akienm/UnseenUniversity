"""
Full-text search tool for the Librarian.

Backends:
  'palace'  — adc.palace nodes (Postgres FTS)
  'indexed' — adc.search_index (folder indexer, Postgres FTS)
  'git'     — git log --grep / git log -S (subprocess)
  None      — union of all available backends
"""

from __future__ import annotations
from unseen_university.identity import home_db_url

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

_GIT_ROOT = Path(__file__).resolve().parents[3]


@dataclass
class SearchResult:
    source: str
    id: str
    score: float
    snippet: str


def _search_palace(query: str, limit: int = 10) -> List[SearchResult]:
    try:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(home_db_url(), connect_timeout=5)
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT path, title, content,
                          ts_rank(to_tsvector('english', coalesce(content,'') || ' ' || coalesce(title,'')),
                                  plainto_tsquery('english', %s)) AS rank
                   FROM adc.palace
                   WHERE to_tsvector('english', coalesce(content,'') || ' ' || coalesce(title,''))
                         @@ plainto_tsquery('english', %s)
                   ORDER BY rank DESC LIMIT %s""",
                (query, query, limit),
            )
            rows = cur.fetchall()
        conn.close()
        return [SearchResult(source="palace", id=r["path"], score=float(r["rank"]),
                             snippet=(r["title"] + ": " + (r["content"] or "")[:200])) for r in rows]
    except Exception as exc:
        logger.debug("search_palace failed: %s", exc)
        return []


def _search_indexed(query: str, limit: int = 10) -> List[SearchResult]:
    try:
        from devices.scraps.jobs.folder_indexer import search_indexed
        rows = search_indexed(query, limit=limit)
        return [SearchResult(source="indexed", id=f"{r['path']}#{r['chunk_index']}",
                             score=float(r.get("rank", 0.5)),
                             snippet=r.get("chunk_text", "")[:300]) for r in rows]
    except Exception as exc:
        logger.debug("search_indexed failed: %s", exc)
        return []


def _search_git(query: str, limit: int = 10) -> List[SearchResult]:
    """Search git commit messages and diffs for query."""
    results = []
    try:
        r = subprocess.run(
            ["git", "log", f"--grep={query}", "--format=%H|%s|%ai", f"-{limit}", "--all"],
            capture_output=True, text=True, timeout=15, cwd=_GIT_ROOT,
        )
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                parts = line.split("|", 2)
                if len(parts) == 3:
                    sha, subject, date = parts
                    results.append(SearchResult(source="git", id=sha[:12], score=0.7,
                                                snippet=f"{sha[:8]} {date[:10]}: {subject}"))
    except Exception as exc:
        logger.debug("search_git (--grep) failed: %s", exc)

    if len(results) < limit:
        try:
            r2 = subprocess.run(
                ["git", "log", f"-S{query}", "--format=%H|%s|%ai", f"-{limit - len(results)}"],
                capture_output=True, text=True, timeout=15, cwd=_GIT_ROOT,
            )
            if r2.returncode == 0:
                seen = {res.id for res in results}
                for line in r2.stdout.splitlines():
                    parts = line.split("|", 2)
                    if len(parts) == 3:
                        sha, subject, date = parts
                        if sha[:12] not in seen:
                            results.append(SearchResult(source="git", id=sha[:12], score=0.65,
                                                        snippet=f"{sha[:8]} {date[:10]} (diff): {subject}"))
                            seen.add(sha[:12])
        except Exception as exc:
            logger.debug("search_git (-S) failed: %s", exc)
    return results[:limit]


async def search(query: str, source: Optional[str] = None, limit: int = 10) -> List[SearchResult]:
    """Search across indexed knowledge.

    Args:
        query: Search query string
        source: Optional filter — 'palace', 'indexed', 'git', or None (all)
        limit: Max results per backend

    Returns:
        Ranked list of SearchResult
    """
    if not query or not query.strip():
        return []
    if source == "palace":
        return _search_palace(query, limit)
    if source == "indexed":
        return _search_indexed(query, limit)
    if source == "git":
        return _search_git(query, limit)
    results: List[SearchResult] = []
    results.extend(_search_palace(query, limit // 2 + 1))
    results.extend(_search_indexed(query, limit // 2 + 1))
    results.extend(_search_git(query, limit // 3 + 1))
    results.sort(key=lambda r: r.score, reverse=True)
    return results[:limit]
