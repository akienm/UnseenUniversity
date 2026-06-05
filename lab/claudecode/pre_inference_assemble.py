#!/usr/bin/env python3
"""
pre_inference_assemble.py — Pre-inference context assembler for sprint-ticket.

Given a ticket ID, assembles a structured context block containing:
  1. Matched design patterns from docs/design_patterns_inventory.md
  2. File symbol maps (via repo_map.py) for all affected files
  3. Domain-term hits — ticket keywords matched against pattern keywords

No LLM involved — pure file reads, AST, regex, and keyword matching.

Usage:
    python3 pre_inference_assemble.py T-xxx
    python3 pre_inference_assemble.py T-xxx --json   # structured JSON output
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

_UU_ROOT = Path(__file__).resolve().parents[2]
_PATTERNS_DOC = _UU_ROOT / "docs" / "design_patterns_inventory.md"
_REPO_MAP = Path(__file__).parent / "repo_map.py"

# Stopwords to exclude from keyword matching
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "can", "to", "of", "in", "for",
    "on", "with", "at", "by", "from", "as", "or", "and", "but", "not",
    "this", "that", "these", "those", "its", "it", "via", "when", "where",
    "which", "who", "what", "how", "if", "then", "all", "any", "each",
    "per", "no", "new", "old", "add", "use", "run", "get", "set", "put",
    "out", "up", "down", "into", "onto", "each", "every", "both", "used",
    "using", "also", "only", "just", "so", "such", "own", "same", "other",
    "after", "before", "during", "within", "without", "between", "about",
    "now", "call", "calls", "called", "returns", "return", "returns",
    "must", "need", "needs", "needed", "make", "makes", "made",
})


def _load_ticket(ticket_id: str) -> dict:
    """Load ticket from cc_queue.py show. Returns parsed dict."""
    cc_queue = Path(__file__).parent / "cc_queue.py"
    result = subprocess.run(
        [sys.executable, str(cc_queue), "show", ticket_id],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"cc_queue.py show {ticket_id} failed: {result.stderr[:200]}")

    out = result.stdout
    # Extract first JSON object from the output (may have extra text after)
    start = out.find("{")
    if start < 0:
        raise SystemExit(f"No JSON in cc_queue output for {ticket_id}")
    brace_depth = 0
    for i, ch in enumerate(out[start:], start):
        if ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth -= 1
            if brace_depth == 0:
                return json.loads(out[start : i + 1])
    raise SystemExit(f"Malformed JSON in cc_queue output for {ticket_id}")


def _extract_affected_files(description: str) -> list[str]:
    """Extract file paths from the Affected files section."""
    m = re.search(r"\*\*Affected files:\*\*\s*(.+?)(?:\n\*\*|\Z)", description, re.S)
    if not m:
        return []
    raw = m.group(1).strip()
    # Split on commas, newlines; strip parenthetical notes and whitespace
    parts = re.split(r"[,\n]+", raw)
    files = []
    for part in parts:
        part = part.strip()
        # Remove parenthetical notes like "(creates new file)"
        part = re.sub(r"\s*\([^)]*\)", "", part).strip()
        if not part or part.startswith("TBD"):
            continue
        files.append(part)
    return files


def _load_patterns() -> list[dict]:
    """Parse PATTERN-xxx entries from the design patterns inventory."""
    if not _PATTERNS_DOC.exists():
        return []
    text = _PATTERNS_DOC.read_text(encoding="utf-8")
    patterns = []
    # Each pattern starts with ## PATTERN-NNN: Title
    for block in re.split(r"\n(?=## PATTERN-)", text):
        m = re.match(r"## (PATTERN-\d+): (.+)", block)
        if not m:
            continue
        pid, title = m.group(1), m.group(2).strip()
        # Extract example file refs from "Canonical examples:" section
        examples_m = re.search(r"\*\*Canonical examples?:\*\*\s*\n((?:- .+\n?)+)", block)
        examples = []
        if examples_m:
            for line in examples_m.group(1).splitlines():
                line = line.strip().lstrip("- ")
                if line:
                    examples.append(line)
        # Keywords = title words + "When to use" content words
        keywords = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", title.lower()))
        when_m = re.search(r"\*\*When to use:\*\*\s*(.+?)(?:\n\*\*|\Z)", block, re.S)
        if when_m:
            keywords |= set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", when_m.group(1).lower()))
        keywords -= _STOPWORDS
        patterns.append({"id": pid, "title": title, "keywords": keywords, "examples": examples, "block": block})
    return patterns


def _ticket_keywords(ticket: dict) -> set[str]:
    """Extract significant keywords from ticket title, tags, and description."""
    sources = [
        ticket.get("title", ""),
        " ".join(ticket.get("tags", [])),
    ]
    desc = ticket.get("description", "")
    # Include description's first 500 chars for context
    sources.append(desc[:500])
    combined = " ".join(sources).lower()
    words = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", combined))
    return words - _STOPWORDS


def _match_patterns(patterns: list[dict], ticket_kw: set[str]) -> list[tuple[int, dict]]:
    """Return patterns sorted by keyword overlap score (highest first)."""
    scored = []
    for p in patterns:
        overlap = ticket_kw & p["keywords"]
        if overlap:
            scored.append((len(overlap), p))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _run_repo_map(file_paths: list[str]) -> str:
    """Run repo_map.py on the given files. Returns text output."""
    if not file_paths:
        return ""
    # Resolve relative to UU root
    resolved = []
    for f in file_paths:
        p = _UU_ROOT / f if not Path(f).is_absolute() else Path(f)
        if p.exists():
            resolved.append(str(p))
        else:
            # Try as-is
            resolved.append(f)
    if not resolved:
        return ""
    result = subprocess.run(
        [sys.executable, str(_REPO_MAP), "--root", str(_UU_ROOT)] + resolved,
        capture_output=True, text=True, cwd=_UU_ROOT,
    )
    return result.stdout.strip() if result.returncode == 0 else f"(repo_map failed: {result.stderr[:100]})"


def assemble(ticket_id: str) -> dict:
    """Assemble pre-inference context for ticket_id. Returns structured dict."""
    ticket = _load_ticket(ticket_id)
    affected_files = _extract_affected_files(ticket.get("description", ""))
    patterns = _load_patterns()
    ticket_kw = _ticket_keywords(ticket)
    matched = _match_patterns(patterns, ticket_kw)
    symbol_map = _run_repo_map(affected_files)

    return {
        "ticket_id": ticket_id,
        "title": ticket.get("title", ""),
        "tags": ticket.get("tags", []),
        "affected_files": affected_files,
        "domain_keywords": sorted(ticket_kw)[:30],
        "matched_patterns": [
            {"id": p["id"], "title": p["title"], "overlap_score": score,
             "examples": p["examples"][:3]}
            for score, p in matched[:5]
        ],
        "symbol_map": symbol_map,
    }


def _format_text(ctx: dict) -> str:
    """Format assembled context as a human-readable text block."""
    lines = [
        f"═══ PRE-INFERENCE CONTEXT: {ctx['ticket_id']} ═══",
        f"Ticket: {ctx['title']}",
        f"Tags:   {', '.join(ctx['tags']) or '(none)'}",
        "",
    ]

    if ctx["affected_files"]:
        lines += ["AFFECTED FILES:", *[f"  {f}" for f in ctx["affected_files"]], ""]

    if ctx["matched_patterns"]:
        lines.append("MATCHED DESIGN PATTERNS:")
        for p in ctx["matched_patterns"]:
            lines.append(f"  [{p['id']}] {p['title']}  (overlap={p['overlap_score']})")
            for ex in p["examples"]:
                lines.append(f"    → {ex}")
        lines.append("")

    if ctx["symbol_map"]:
        lines += ["FILE SYMBOL MAP:", ctx["symbol_map"], ""]

    if ctx["domain_keywords"]:
        lines.append(f"DOMAIN TERMS: {', '.join(ctx['domain_keywords'][:20])}")

    lines.append("═" * 50)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ticket_id")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    args = parser.parse_args()

    ctx = assemble(args.ticket_id)

    if args.json:
        print(json.dumps(ctx, indent=2))
    else:
        print(_format_text(ctx))


if __name__ == "__main__":
    main()
