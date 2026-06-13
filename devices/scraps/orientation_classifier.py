"""
orientation_classifier.py — Graph-tree orientation classifier for ticket sprints.

Given a ticket dict, queries clan.code_index to find relevant files/symbols
and emits a BuilderReport. Called by ToolLoop before first tool use so the
model starts with a map of the codebase rather than exploring blind.

Fail-open: if the DB is unavailable, returns an empty BuilderReport and the
caller continues without it. No cloud calls — local Postgres only.

D-orientation-classifier-arch-2026-06-13
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass, field

log = logging.getLogger(__name__)

_DB_URL = os.environ.get(
    "UU_HOME_DB_URL",
    os.environ.get("IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"),
)

_STOP_WORDS = frozenset([
    "the", "and", "or", "but", "for", "not", "with", "this", "that",
    "from", "into", "via", "per", "use", "used", "using", "in", "on",
    "to", "of", "is", "it", "its", "be", "by", "as", "are", "was", "were",
    "will", "can", "all", "any", "each", "also", "have", "has", "had",
    "out", "up", "if", "else", "new", "add", "run", "set", "get", "put",
    "should", "would", "could", "does", "make", "when", "then", "than",
    "after", "before", "how", "what", "where", "which", "who", "been",
    "being", "their", "them", "they", "you", "your", "we", "our", "my",
    "ticket", "task", "work", "done", "must", "must", "only", "file",
    "files", "test", "tests", "code", "data", "type", "none", "true",
    "false", "class", "return", "import", "def", "function", "method",
])

_MIN_KW_LEN = 4
_MAX_KEYWORDS = 20
_MAX_DB_ROWS = 200
_MAX_RELEVANT_FILES = 10


# ── Data types ─────────────────────────────────────────────────────────────────


@dataclass
class FileMatch:
    path: str
    symbol: str
    kind: str
    summary: str
    score: float

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "symbol": self.symbol,
            "kind": self.kind,
            "summary": self.summary[:150],
            "score": round(self.score, 2),
        }


@dataclass
class BuilderReport:
    keywords: list[str] = field(default_factory=list)
    relevant_files: list[dict] = field(default_factory=list)
    task_shape: str = "general"
    estimated_complexity: str = "M"

    def to_dict(self) -> dict:
        return asdict(self)

    def to_text(self) -> str:
        """Format as a text block prepended to the ToolLoop user message."""
        if not self.relevant_files:
            return ""
        lines = [
            "## Builder Report (orientation classifier)",
            f"Task shape: {self.task_shape}  Complexity: {self.estimated_complexity}",
            f"Keywords matched: {', '.join(self.keywords[:10])}",
            "",
            "Relevant files — start here before grepping:",
        ]
        for i, f in enumerate(self.relevant_files[:8], 1):
            sym = f.get("symbol", "")
            kind = f.get("kind", "")
            hint = f" [{sym} / {kind}]" if sym and sym != "__file_intent__" else ""
            lines.append(f"  {i}. {f['path']}{hint}")
            summary = f.get("summary", "")
            if summary and summary != f"shell script: {f['path'].split('/')[-1]}":
                lines.append(f"     {summary[:120]}")
        return "\n".join(lines)


# ── Keyword extraction ─────────────────────────────────────────────────────────


def extract_keywords(ticket: dict) -> list[str]:
    """Extract meaningful words from ticket title + tags + description (first 300 chars)."""
    title = ticket.get("title", "")
    tags = ticket.get("tags", [])
    desc = ticket.get("description", "")[:300]

    combined = f"{title} {' '.join(tags)} {desc}"
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_]*", combined)

    seen: set[str] = set()
    keywords: list[str] = []
    for w in words:
        w_lower = w.lower()
        if (len(w) >= _MIN_KW_LEN
                and w_lower not in _STOP_WORDS
                and w_lower not in seen):
            keywords.append(w)
            seen.add(w_lower)

    return keywords[:_MAX_KEYWORDS]


# ── Task shape classification ──────────────────────────────────────────────────


def classify_task_shape(ticket: dict) -> str:
    """Infer task shape from tags and title prefix."""
    tags = {t.lower() for t in ticket.get("tags", [])}
    title = ticket.get("title", "").lower()

    if tags & {"bug", "fix", "regression", "hotfix"} or title.startswith("[fix"):
        return "bug-fix"
    if tags & {"refactor", "cleanup", "rename", "migration"} or "refactor" in title:
        return "refactor"
    if tags & {"docs", "documentation", "docstring"}:
        return "docs"
    if tags & {"test", "tests", "testing", "qa"}:
        return "test"
    if tags & {"feature", "enhancement"} or title.startswith("[feat"):
        return "new-feature"
    if tags & {"config", "yaml", "settings"}:
        return "config"
    return "general"


# ── DB query ───────────────────────────────────────────────────────────────────


def query_relevant_files(keywords: list[str], db_url: str) -> list[FileMatch]:
    """
    Query clan.code_index for rows matching any keyword in path/symbol/summary.
    Returns FileMatch list sorted by relevance score (desc).
    """
    if not keywords:
        return []

    import psycopg2

    # Build WHERE: any row where at least one keyword matches any of the three columns
    conditions = []
    params: list[str] = []
    for kw in keywords:
        pattern = f"%{kw}%"
        conditions.append("(path ILIKE %s OR symbol ILIKE %s OR summary ILIKE %s)")
        params.extend([pattern, pattern, pattern])

    where = " OR ".join(conditions)

    conn = psycopg2.connect(db_url)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT path, symbol, kind, summary FROM clan.code_index WHERE {where} LIMIT %s",
                params + [_MAX_DB_ROWS],
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    log.info("ORIENTATION|query|keywords=%d|rows=%d", len(keywords), len(rows))

    # Score in Python: symbol match worth 2, path/summary match worth 1
    kw_lower = [k.lower() for k in keywords]
    scored: list[FileMatch] = []
    for path, symbol, kind, summary in rows:
        combined = f"{path or ''} {symbol or ''} {summary or ''}".lower()
        score = sum(
            (2 if kw in (symbol or "").lower() else 1)
            for kw in kw_lower
            if kw in combined
        )
        scored.append(FileMatch(
            path=path or "",
            symbol=symbol or "",
            kind=kind or "",
            summary=(summary or "")[:200],
            score=float(score),
        ))

    # Deduplicate by path, keeping highest-scored row per path
    by_path: dict[str, FileMatch] = {}
    for m in scored:
        if m.path not in by_path or m.score > by_path[m.path].score:
            by_path[m.path] = m

    return sorted(by_path.values(), key=lambda x: (-x.score, x.path))[:_MAX_RELEVANT_FILES]


# ── Main entry point ───────────────────────────────────────────────────────────


def classify(ticket: dict, db_url: str | None = None) -> BuilderReport:
    """
    Classify a ticket and return a BuilderReport.
    Fail-open: DB failure returns an empty BuilderReport.

    Interface crossing log: INFO with ticket id and match count.
    """
    if db_url is None:
        db_url = _DB_URL

    keywords = extract_keywords(ticket)
    task_shape = classify_task_shape(ticket)
    complexity = ticket.get("size", "M") or "M"

    try:
        matches = query_relevant_files(keywords, db_url)
        log.info(
            "ORIENTATION|ticket=%s|keywords=%d|matches=%d|shape=%s",
            ticket.get("id", "?"), len(keywords), len(matches), task_shape,
        )
    except Exception as exc:
        log.warning("ORIENTATION|ticket=%s|db_error=%s — returning empty report", ticket.get("id", "?"), exc)
        matches = []

    return BuilderReport(
        keywords=keywords[:10],
        relevant_files=[m.to_dict() for m in matches],
        task_shape=task_shape,
        estimated_complexity=complexity,
    )
