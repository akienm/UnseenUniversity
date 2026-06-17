#!/usr/bin/env python3
"""
cc_nightly_learning.py — Extract feedback from CC session transcripts and update memory tree.

Reads Claude Code JSONL transcripts, identifies feedback signals (explicit corrections,
approvals, CC++ marks), and writes new memory/*.md entries for novel patterns.

Usage:
    python3 cc_nightly_learning.py [--date YYYY-MM-DD] [--transcript path/to/file.jsonl]
    python3 cc_nightly_learning.py --dry-run   # preview without writing
    python3 cc_nightly_learning.py             # default: today's transcripts

Output: new memory/*.md files + MEMORY.md index entries for novel feedback.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects" / "-home-akien-dev-src-UnseenUniversity"
MEMORY_DIR = PROJECTS_DIR / "memory"
MEMORY_INDEX = MEMORY_DIR / "MEMORY.md"

# Feedback patterns: (label, regex, type)
# Order matters: more specific first.
_FEEDBACK_PATTERNS = [
    ("cc_plus_plus", r"\bCC\+\+\b", "feedback", "positive"),
    ("cc_plus", r"\bCC\+\b", "feedback", "positive"),
    ("stop_doing", r"\bstop\s+doing\b|\bdon['']t\s+do\s+that\b|\bnever\s+do\s+that\b", "feedback", "correction"),
    ("dont_pattern", r"\bdon['']t\b.{0,40}\b(again|ever|always)\b|\bnever\b.{0,40}\b(do|use|add|create|make)\b", "feedback", "correction"),
    ("wrong_approach", r"\bwrong\s+(way|approach|pattern)\b|\bnot\s+that\b|\binstead\b.{0,60}", "feedback", "correction"),
    ("explicit_correction", r"\byou\s+should\s+(never|not|always)\b|\bwe\s+(never|don['']t|shouldn['']t)\b", "feedback", "correction"),
    ("good_work", r"\b(good\s+work|well\s+done|perfect|exactly\s+right|that['']s\s+(right|perfect))\b", "feedback", "positive"),
    ("yes_exactly", r"\byes[,.]?\s+exactly\b|\bagreed[,.]?\s+(perfect|exactly|great)\b|\bperfect[,.]?\s+keep\b", "feedback", "positive"),
    ("design_approval", r"^(agreed|ok|yes|sounds good|that works|let['']s go|go for it|do it)[.!]?\s*$", "feedback", "approval"),
    ("go_signal", r"\bgo\s+go\s+go\b|\bgo\s+ahead\b|\bplease\s+do\b", "feedback", "positive"),
    ("is_never", r"\bis\s+never\s+(an?|the)\b|\bnever\s+the\s+\w+\s+for\b", "feedback", "correction"),
    ("we_already", r"\bwe\s+already\s+(built|have|created|made|did)\b|\bwe\s+already\s+have\s+a\b", "feedback", "correction"),
    ("we_just_said", r"\bwe\s+just\s+said\b|\byou\s+just\s+said\b|\bwe\s+said\s+no\b", "feedback", "correction"),
    ("tagged_igor", r"tagged\s+(as\s+)?igor|tagged\s+igor|\bno\s+tickets\s+tagged", "feedback", "correction"),
    ("priority_pref", r"\bmore\s+interested\s+in\b|\bi\s+prefer\b|\bi[`']m\s+much\s+more\s+interested\b", "user", "preference"),
]

# Explicit short-circuit: messages that are pure commands or boilerplate, skip them
_SKIP_PATTERNS = [
    r"^<command",
    r"^<local-command",
    r"^\[Image",
    r"^hello\?$",
    r"^Base directory for this skill:",
    r"^This session is being continued",
    r"^#\s+",           # markdown headers (skill content)
    r"^---\n",          # frontmatter blocks
    r"^preserve:",      # autocompact preserve strings
    r"^\* \[",          # bullet link lists (skill TOC)
    r"^Step \d+[:\.]",  # numbered skill steps
]

# Maximum character length for a user message to be considered real user input.
# Skill injections and system messages tend to be much longer.
_MAX_USER_MSG_LEN = 1000


def _skip_msg(text: str) -> bool:
    t = text.strip()
    if len(t) > _MAX_USER_MSG_LEN:
        return True
    for p in _SKIP_PATTERNS:
        if re.search(p, t, re.I | re.M):
            return True
    return False


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


def load_transcript(path: Path) -> list[dict]:
    """Load a JSONL transcript; return list of {'role': 'user'|'assistant', 'text': str}."""
    turns = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception as e:
        print(f"[warn] could not read {path}: {e}", file=sys.stderr)
        return turns

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
        if text:
            turns.append({"role": t, "text": text})
    return turns


def find_transcripts(date: str | None = None, specific: Path | None = None) -> list[Path]:
    """Return transcript paths to process (today's or specific)."""
    if specific:
        return [specific]
    if not PROJECTS_DIR.exists():
        return []

    jsonls = sorted(PROJECTS_DIR.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True)
    if not jsonls:
        return []

    if date is None:
        # Today's: files modified today
        today = datetime.now().strftime("%Y-%m-%d")
        result = []
        for j in jsonls:
            mtime = datetime.fromtimestamp(j.stat().st_mtime).strftime("%Y-%m-%d")
            if mtime == today:
                result.append(j)
        # Fallback: most recent file if nothing from today
        if not result:
            result = [jsonls[0]]
        return result

    # Specific date
    result = []
    for j in jsonls:
        mtime = datetime.fromtimestamp(j.stat().st_mtime).strftime("%Y-%m-%d")
        if mtime == date:
            result.append(j)
    return result


def detect_feedback(turns: list[dict]) -> list[dict]:
    """Scan turns for feedback signals. Returns list of feedback items."""
    items = []
    for i, turn in enumerate(turns):
        if turn["role"] != "user":
            continue
        text = turn["text"]
        if _skip_msg(text):
            continue

        for label, pattern, mem_type, valence in _FEEDBACK_PATTERNS:
            if re.search(pattern, text, re.I | re.S):
                # Get preceding assistant context (up to 2 turns back)
                ctx = ""
                for j in range(i - 1, max(i - 4, -1), -1):
                    if turns[j]["role"] == "assistant":
                        ctx = turns[j]["text"][:600]
                        break

                items.append({
                    "label": label,
                    "valence": valence,
                    "type": mem_type,
                    "user_text": text[:300],
                    "context": ctx,
                    "pattern": pattern,
                })
                break  # Only first matching pattern per turn

    return items


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9\s-]", "", text.lower())
    slug = re.sub(r"\s+", "-", slug.strip())
    return slug[:50].rstrip("-")


def _existing_memory_names() -> set[str]:
    """Return set of existing memory file name slugs (without .md)."""
    if not MEMORY_DIR.exists():
        return set()
    return {p.stem for p in MEMORY_DIR.glob("*.md")}


def _existing_index_entries() -> set[str]:
    """Return set of file hrefs already in MEMORY.md."""
    if not MEMORY_INDEX.exists():
        return set()
    text = MEMORY_INDEX.read_text(encoding="utf-8")
    return set(re.findall(r'\((\w+\.md)\)', text))


def _generate_memory_entry(item: dict, session_id: str) -> dict | None:
    """Convert a raw feedback item into a structured memory entry."""
    user_text = item["user_text"].strip()
    context = item["context"].strip()
    valence = item["valence"]
    label = item["label"]

    # Take only the first two sentences of the user message as the core rule.
    # Strip trailing ticket-list noise (lines starting with T- or [).
    lines = user_text.splitlines()
    core_lines = []
    for ln in lines:
        stripped = ln.strip()
        if re.match(r'^T-\w|^\[', stripped):
            break
        core_lines.append(stripped)
    core_text = " ".join(core_lines).strip()
    # Strip inline ticket-list noise: T-xxx trailing content after the first sentence
    core_text = re.split(r'\s{2,}T-\w', core_text)[0].strip()
    # Truncate to first 2 sentences if long
    sentences = re.split(r'(?<=[.!?])\s+', core_text)
    rule = " ".join(sentences[:2]) if len(sentences) > 2 else core_text
    rule = re.sub(r'\s+', ' ', rule)

    # Build a short title/name
    if valence == "correction":
        prefix = "feedback_correction"
        mem_type = "feedback"
    elif valence in ("positive", "approval"):
        prefix = "feedback_approval"
        mem_type = "feedback"
    else:
        prefix = "feedback"
        mem_type = "feedback"

    # Extract the core subject from user text for slug
    clean = re.sub(r'[<>"\'/\\]', '', user_text)
    clean = re.sub(r'\s+', ' ', clean)[:40]
    slug = f"{prefix}_{_slugify(clean)}"

    # Short one-liner for description (use cleaned rule text)
    description = rule[:100].replace('\n', ' ')

    body_lines = [rule, ""]
    if context:
        body_lines.append(f"**Why:** (from session context) {context[:200]}")
    else:
        body_lines.append("**Why:** (no preceding context captured)")
    body_lines.append("")
    body_lines.append("**How to apply:** Review this feedback before similar work.")

    return {
        "slug": slug,
        "description": description,
        "type": mem_type,
        "session_id": session_id,
        "body": "\n".join(body_lines),
        "index_line": f"- [{description[:60]}]({slug}.md) — {description[:80]}",
    }


def write_memory_file(entry: dict, dry_run: bool = False) -> bool:
    """Write a single memory file. Returns True if written (or would be)."""
    slug = entry["slug"]
    path = MEMORY_DIR / f"{slug}.md"

    if path.exists():
        return False  # Already exists — skip

    content = f"""---
name: {slug}
description: {entry['description'][:100]}
metadata:
  type: {entry['type']}
  originSessionId: {entry['session_id']}
---

{entry['body']}
"""
    if dry_run:
        print(f"[DRY RUN] would write {path.name}:")
        print(content[:200])
        return True

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def update_memory_index(new_entries: list[dict], dry_run: bool = False) -> int:
    """Append new entries to MEMORY.md. Returns count added."""
    if not new_entries:
        return 0

    existing_hrefs = _existing_index_entries()
    to_add = [e for e in new_entries if f"{e['slug']}.md" not in existing_hrefs]
    if not to_add:
        return 0

    lines = "\n".join(e["index_line"] for e in to_add)

    if dry_run:
        print(f"[DRY RUN] would append {len(to_add)} lines to MEMORY.md")
        return len(to_add)

    with MEMORY_INDEX.open("a", encoding="utf-8") as f:
        f.write("\n" + lines + "\n")
    return len(to_add)


def run(date: str | None = None, specific: Path | None = None, dry_run: bool = False) -> int:
    """Main entry: find transcripts, extract feedback, write memory. Returns feedback item count."""
    transcripts = find_transcripts(date=date, specific=specific)
    if not transcripts:
        print("no transcripts found", file=sys.stderr)
        return 0

    print(f"processing {len(transcripts)} transcript(s)")
    existing = _existing_memory_names()

    all_items = []
    for path in transcripts:
        session_id = path.stem
        print(f"  {path.name}: ", end="", flush=True)
        turns = load_transcript(path)
        items = detect_feedback(turns)
        print(f"{len(turns)} turns, {len(items)} feedback signals")
        for item in items:
            entry = _generate_memory_entry(item, session_id)
            if entry and entry["slug"] not in existing:
                all_items.append(entry)
                existing.add(entry["slug"])

    written = 0
    for entry in all_items:
        if write_memory_file(entry, dry_run=dry_run):
            written += 1
            print(f"  wrote: {entry['slug']}.md")

    indexed = update_memory_index(all_items, dry_run=dry_run)

    print(f"\nsummary: {len(all_items)} novel items found, {written} files written, {indexed} index entries added")
    return len(all_items)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="Date YYYY-MM-DD to scan (default: today)")
    parser.add_argument("--transcript", help="Specific transcript file path")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    specific = Path(args.transcript) if args.transcript else None
    count = run(date=args.date, specific=specific, dry_run=args.dry_run)
    print(f"\n{count} feedback item(s) extracted")
    sys.exit(0 if count >= 0 else 1)


if __name__ == "__main__":
    main()
