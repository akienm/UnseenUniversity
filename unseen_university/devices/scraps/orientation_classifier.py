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
from unseen_university.identity import home_db_url

import logging
import os
import re
from dataclasses import asdict, dataclass, field

log = logging.getLogger(__name__)

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

# Signature-map budget (T-coding-repo-map-orientation): the model plans from STRUCTURE
# (signatures) instead of reading bodies, so the map lists several key symbols per file
# within a bounded size (aider's repo-map default is ~1k tokens; chars ≈ 3.5× that).
_MAX_MAP_FILES = 8
_MAX_SYMBOLS_PER_FILE = 6
_MAP_CHAR_BUDGET = 3500


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


# ── Signature map (structure without bodies) ─────────────────────────────────────


def _signature_of(summary: str, symbol: str, kind: str) -> str:
    """Extract the signature line from a code_index summary.

    Summaries are stored as '<signature> — <description>' (e.g.
    'def parse_widget(data: dict) — parse the widget'); the signature is the part before
    the em-dash. Fall back to a synthesized 'class/def <symbol>' when no signature is stored.
    """
    head = (summary or "").split(" — ", 1)[0].strip()
    if head:
        return head
    kw = "class" if kind == "class" else "def"
    return f"{kw} {symbol}".strip()


def query_file_symbols(keywords: list[str], db_url: str) -> dict[str, list[FileMatch]]:
    """Query clan.code_index and group matches BY FILE, keeping multiple symbols per file.

    Unlike query_relevant_files (which dedups to one FileMatch per path), this preserves
    every matching symbol so the caller can render a per-file signature list. Symbols within
    a file are sorted by score (desc); the dict is unordered (the caller ranks files).
    """
    if not keywords:
        return {}

    import psycopg2

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

    kw_lower = [k.lower() for k in keywords]
    by_path: dict[str, list[FileMatch]] = {}
    for path, symbol, kind, summary in rows:
        combined = f"{path or ''} {symbol or ''} {summary or ''}".lower()
        score = sum(
            (2 if kw in (symbol or "").lower() else 1)
            for kw in kw_lower
            if kw in combined
        )
        if score <= 0:
            continue
        by_path.setdefault(path or "", []).append(FileMatch(
            path=path or "", symbol=symbol or "", kind=kind or "",
            summary=(summary or "")[:200], score=float(score),
        ))
    for syms in by_path.values():
        syms.sort(key=lambda m: (-m.score, m.symbol))
    log.info("SIGNATURE_MAP|query|keywords=%d|files=%d", len(keywords), len(by_path))
    return by_path


def build_signature_map(ticket: dict, db_url: str | None = None) -> str:
    """Build a token-budgeted signature map of the ticket-relevant repo structure.

    D-coding-loop-redesign-aider-survey. The model plans from STRUCTURE — the key classes/
    functions and their signatures across the relevant files — instead of opening files to
    discover it (the read-wander a weak model falls into; 2026-07-04 DS.0 observe: 47-102
    Reads, 0 edits). Files are RELEVANCE-ranked (aggregate keyword score); true
    dependency-graph rank is a follow-up (code_index carries no edge data). Bounded by
    _MAP_CHAR_BUDGET. Fail-open: any DB error → '' and the loop continues without it.
    """
    if db_url is None:
        db_url = home_db_url()
    keywords = extract_keywords(ticket)
    try:
        grouped = query_file_symbols(keywords, db_url)
    except Exception as exc:
        log.warning("SIGNATURE_MAP|ticket=%s|db_error=%s — empty map", ticket.get("id", "?"), exc)
        return ""
    if not grouped:
        return ""

    # Rank files by aggregate relevance (sum of their symbols' scores), tie-break by path.
    ranked = sorted(grouped.items(), key=lambda kv: (-sum(m.score for m in kv[1]), kv[0]))

    header = (
        "## Repo signature map (plan from this structure; read a file only if the map is "
        "insufficient)"
    )
    lines = [header]
    used = len(header)
    files_shown = 0
    for path, syms in ranked[:_MAX_MAP_FILES]:
        block_lines = [path]
        for m in syms[:_MAX_SYMBOLS_PER_FILE]:
            block_lines.append(f"  {_signature_of(m.summary, m.symbol, m.kind)}")
        block = "\n".join(block_lines)
        # Budget: always show at least one file; stop before exceeding the cap afterwards.
        if files_shown > 0 and used + len(block) + 1 > _MAP_CHAR_BUDGET:
            break
        lines.append(block)
        used += len(block) + 1
        files_shown += 1

    log.info("SIGNATURE_MAP|ticket=%s|files=%d|chars=%d", ticket.get("id", "?"), files_shown, used)
    return "\n".join(lines) + "\n\n"


# ── Main entry point ───────────────────────────────────────────────────────────


def classify(ticket: dict, db_url: str | None = None) -> BuilderReport:
    """
    Classify a ticket and return a BuilderReport.
    Fail-open: DB failure returns an empty BuilderReport.

    Interface crossing log: INFO with ticket id and match count.
    """
    if db_url is None:
        db_url = home_db_url()

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
