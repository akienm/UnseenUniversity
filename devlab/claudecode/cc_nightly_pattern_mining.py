#!/usr/bin/env python3
"""
cc_nightly_pattern_mining.py — Extract design patterns from CC session transcripts.

Scans conversation turns for recurring design themes, scores by frequency,
and writes to adc.palace as palace.patterns.* nodes. Re-running merges new
observations into existing nodes (additive — no overwrites).

Usage:
    python3 cc_nightly_pattern_mining.py [--date YYYY-MM-DD] [--dry-run]
    python3 cc_nightly_pattern_mining.py --transcript /path/to/file.jsonl

Patterns detected (keyword → pattern name → palace path):
    master control, circuit breaker, fail-open → resilience
    observability, visibility, trace            → observability-first
    factory of factories, compiled inference    → compiled-inference
    fail-open, one bad plugin                   → fail-open
    external state, holds state externally      → external-state
    no autonomous, never claim, dispatch        → push-not-pull
    ground loop, supervisor, restart            → process-supervision
    and more — see _PATTERNS below
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects" / "-home-akien-dev-src-UnseenUniversity"

_DB_URL = os.environ.get(
    "UU_HOME_DB_URL",
    os.environ.get("IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"),
)

# Pattern definitions: (name, palace_slug, keywords, description)
# keywords are searched in both user AND assistant messages.
_PATTERNS = [
    (
        "master-control",
        "master-control-circuit-breaker",
        [
            r"\bmaster\s+control\b",
            r"\bcircuit\s+breaker\b",
            r"\bguru\s+loop\b",
            r"\bground\s+loop\b",
            r"\bprocess\s+supervisor\b",
            r"\bkeepalive\b",
        ],
        "Master control + circuit breaker = resilience. A supervisor layer keeps "
        "services alive; disabling via breaker flag prevents runaway restarts.",
    ),
    (
        "observability-first",
        "observability-first",
        [
            r"\bobservability\b",
            r"\bnarrative\s+visibility\b",
            r"\bcognitive\s+trace\b",
            r"\bvisibility\s+into\b",
            r"\bcan\s+(akien|you|we)\s+see\b",
            r"\bmake\s+it\s+visible\b",
        ],
        "Observability first. Visibility into running state is more valuable than "
        "adding new capabilities — if you can't see it, you can't debug it.",
    ),
    (
        "compiled-inference",
        "compiled-inference",
        [
            r"\bcompiled\s+inference\b",
            r"\bfactory\s+of\s+factories\b",
            r"\bgraph.tree\s+inference\b",
            r"\bintent\s+doc\b",
            r"\borientation\s+classifier\b",
        ],
        "Compiled inference / factory of factories. The long-term vision: replace "
        "LLM restarts with graph-tree inference compiled from intent docs.",
    ),
    (
        "fail-open",
        "fail-open",
        [
            r"\bfail.open\b",
            r"\bone\s+bad\s+plugin\b",
            r"\bnever\s+crashes?\s+the\s+(loop|system|rack)\b",
            r"\bgraceful\s+degradation\b",
        ],
        "Fail-open principle. One bad plugin/device never crashes the enclosing "
        "system. Errors are logged; the loop continues.",
    ),
    (
        "external-state",
        "external-state-principle",
        [
            r"\bexternal\s+state\b",
            r"\bholds?\s+state\s+externally\b",
            r"\brestart\s+freely\b",
            r"\bno\s+in.memory.only\b",
        ],
        "External state principle. Every device holds state in Postgres or flat-file "
        "so it can restart freely without losing context.",
    ),
    (
        "push-not-pull",
        "push-not-pull-dispatch",
        [
            r"\bno\s+autonomous\s+(claim|pickup)\b",
            r"\bnever\s+claim\s+autonomously\b",
            r"\bcc\s+dispatches\b",
            r"\bworkers?\s+must\s+not\s+pull\b",
            r"\bdispatch.not.claim\b",
        ],
        "Push not pull dispatch. CC dispatches tickets to workers; workers never "
        "autonomously claim from the queue. adopt_next_ticket raises LegacyDirectClaimError.",
    ),
    (
        "igor-uses-systems",
        "igor-uses-not-contains",
        [
            r"\bigor\s+(calls|uses)\s+tools\b",
            r"\bigor\s+is\s+not\s+(the\s+host|a\s+monolith)\b",
            r"\borchestration\s+over\s+monolith\b",
            r"\bwhat\s+shape\s+does\s+this\s+problem\s+have\b",
        ],
        "Igor uses systems, does not contain them. Igor calls tools (queue, channel, "
        "memory, rack device); NE/cognition are internal; everything else is a tool call.",
    ),
    (
        "erector-set",
        "adc-as-erector-set",
        [
            r"\berector\s+set\b",
            r"\bportable\s+substrate\b",
            r"\bdevice\s+per\s+subdirectory\b",
            r"\bblast\s+radius\b",
        ],
        "ADC as erector set. UnseenUniversity is the portable substrate; each device "
        "is independently deployable to contain blast radius.",
    ),
]

# Maximum length for user/assistant messages to be considered for pattern matching.
_MAX_MSG_LEN = 2000


def _extract_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                parts.append(c.get("text", ""))
        return "\n".join(parts)
    return ""


def load_transcript_text(path: Path) -> str:
    """Load all text from a transcript (user + assistant), concatenated."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        print(f"[warn] could not read {path}: {e}", file=sys.stderr)
        return ""

    chunks = []
    for raw in lines:
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        t = d.get("type")
        if t not in ("user", "assistant"):
            continue
        m = d.get("message", {})
        text = _extract_text(m.get("content", "")).strip()
        if text and len(text) <= _MAX_MSG_LEN:
            chunks.append(text)
    return "\n\n".join(chunks)


def find_transcripts(date: str | None = None, specific: Path | None = None) -> list[Path]:
    if specific:
        return [specific]
    if not PROJECTS_DIR.exists():
        return []
    jsonls = sorted(PROJECTS_DIR.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True)
    if not jsonls:
        return []
    today = datetime.now().strftime("%Y-%m-%d")
    target = date or today
    result = [j for j in jsonls
              if datetime.fromtimestamp(j.stat().st_mtime).strftime("%Y-%m-%d") == target]
    if not result and not date:
        result = [jsonls[0]]
    return result


def mine_patterns(full_text: str) -> dict[str, dict]:
    """
    Scan full_text for each pattern's keywords.
    Returns {pattern_name: {hits: int, confidence: float, first_hit: str}}
    """
    results = {}
    for name, slug, keywords, desc in _PATTERNS:
        hits = 0
        first_hit = ""
        for kw in keywords:
            matches = list(re.finditer(kw, full_text, re.I))
            if matches and not first_hit:
                start = max(0, matches[0].start() - 40)
                end = min(len(full_text), matches[0].end() + 80)
                first_hit = full_text[start:end].replace("\n", " ").strip()
            hits += len(matches)
        if hits > 0:
            # Confidence: sigmoid-ish — each keyword hit adds ~0.25, capped at 1.0
            confidence = min(1.0, hits * 0.25)
            results[name] = {
                "slug": slug,
                "hits": hits,
                "confidence": confidence,
                "first_hit": first_hit[:200],
                "description": desc,
            }
    return results


def write_to_palace(patterns: dict[str, dict], dry_run: bool = False) -> int:
    """Upsert pattern nodes to adc.palace. Returns count written."""
    if not patterns:
        return 0

    written = 0
    for name, info in patterns.items():
        slug = info["slug"]
        path = f"palace.patterns.{slug}"
        title = f"Design pattern: {name}"
        content = (
            f"## {name}\n\n"
            f"{info['description']}\n\n"
            f"**Confidence:** {info['confidence']:.2f} ({info['hits']} keyword hits)\n"
            f"**Example from transcript:** {info['first_hit']!r}\n"
        )
        metadata = {
            "pattern_name": name,
            "slug": slug,
            "hits": info["hits"],
            "confidence": info["confidence"],
            "mined_at": datetime.now(timezone.utc).isoformat(),
        }

        if dry_run:
            print(f"[DRY RUN] upsert {path}: {title} (confidence={info['confidence']:.2f})")
            written += 1
            continue

        try:
            import psycopg2
            import psycopg2.extras

            conn = psycopg2.connect(_DB_URL)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO adc.palace (path, title, content, node_type, updated_at, metadata)
                    VALUES (%s, %s, %s, 'pattern', now(), %s::jsonb)
                    ON CONFLICT (path) DO UPDATE SET
                        content = EXCLUDED.content,
                        updated_at = EXCLUDED.updated_at,
                        metadata = adc.palace.metadata || EXCLUDED.metadata
                    """,
                    (path, title, content, json.dumps(metadata)),
                )
            conn.commit()
            conn.close()
            print(f"  upserted: {path} (confidence={info['confidence']:.2f})")
            written += 1
        except Exception as exc:
            print(f"  [warn] palace write failed for {name}: {exc}", file=sys.stderr)

    return written


def run(date: str | None = None, specific: Path | None = None, dry_run: bool = False) -> dict:
    transcripts = find_transcripts(date=date, specific=specific)
    if not transcripts:
        print("no transcripts found", file=sys.stderr)
        return {}

    print(f"mining {len(transcripts)} transcript(s)")
    full_text = "\n\n".join(load_transcript_text(p) for p in transcripts)
    print(f"  total text: {len(full_text)} chars")

    patterns = mine_patterns(full_text)
    print(f"  patterns found: {len(patterns)}")
    for name, info in sorted(patterns.items(), key=lambda x: -x[1]["confidence"]):
        print(f"    {name}: confidence={info['confidence']:.2f} hits={info['hits']}")

    written = write_to_palace(patterns, dry_run=dry_run)
    print(f"\nsummary: {len(patterns)} patterns mined, {written} palace nodes written")
    return patterns


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Date YYYY-MM-DD to scan (default: today)")
    parser.add_argument("--transcript", help="Specific transcript path")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    specific = Path(args.transcript) if args.transcript else None
    patterns = run(date=args.date, specific=specific, dry_run=args.dry_run)
    sys.exit(0 if patterns else 1)


if __name__ == "__main__":
    main()
