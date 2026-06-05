#!/usr/bin/env python3
"""
Proactive pre-sprint briefing assembler.

Before CC reads any files or forms a plan, this script surfaces:
  1. Prior tickets sharing the same decision_id (directly related work)
  2. Prior tickets that touched the same affected files (file-proximity signal)
  3. Escalation summaries from this ticket (if it has been escalated before)
  4. Reset count history (how many times this ticket has been attempted)

Output is kept under ~500 tokens to stay within the pre-brief budget.

Usage:
    sprint_preflight_brief.py <ticket_id>
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path


def _load_queue():
    cc_tools = os.environ.get(
        "CC_WORKFLOW_TOOLS",
        str(Path.home() / "dev/src/UnseenUniversity/lab/claudecode"),
    )
    spec = importlib.util.spec_from_file_location(
        "cc_queue", str(Path(cc_tools) / "cc_queue.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _extract_affected_files(description: str) -> list[str]:
    """Parse 'Affected files: path1, path2, ...' from ticket description."""
    m = re.search(r"\*\*Affected files:\*\*\s*(.+?)(?:\n|\*\*|$)", description, re.S)
    if not m:
        return []
    raw = m.group(1).strip()
    # Split on comma or newline, strip whitespace
    files = [f.strip() for f in re.split(r"[,\n]", raw) if f.strip()]
    # Filter out TBD/none entries
    return [f for f in files if not re.match(r"(?i)^(tbd|none|n/a)", f)]


def _truncate(text: str, max_chars: int = 300) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "…"


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: sprint_preflight_brief.py <ticket_id>", file=sys.stderr)
        sys.exit(1)

    ticket_id = sys.argv[1]

    try:
        q = _load_queue()
    except Exception as e:
        print(f"Pre-brief: queue unavailable ({e}) — skipping")
        return

    try:
        all_tickets = q._load()
    except Exception as e:
        print(f"Pre-brief: could not load tickets ({e}) — skipping")
        return

    ticket = next((t for t in all_tickets if t.get("id") == ticket_id), None)
    if not ticket:
        print(f"Pre-brief: ticket {ticket_id} not found — skipping")
        return

    lines: list[str] = []
    terminal = {"done", "closed", "cancelled", "discarded"}

    # ── Reset count + escalation history ──────────────────────────────────────
    reset_count = int(ticket.get("reset_count") or 0)
    if reset_count > 0:
        lines.append(f"⚠ RESET COUNT: {reset_count} — this ticket has been attempted before.")
        # Surface escalation summary if present in description
        desc = ticket.get("description", "")
        esc_m = re.search(r"## Escalation summary(.+?)(?=##|$)", desc, re.S)
        if esc_m:
            lines.append("Prior escalation summary:")
            lines.append(_truncate(esc_m.group(1).strip(), 400))

    # ── Related tickets by decision_id ────────────────────────────────────────
    decision_id = ticket.get("decision_id")
    if decision_id:
        related = [
            t for t in all_tickets
            if t.get("decision_id") == decision_id
            and t.get("id") != ticket_id
            and t.get("status") not in terminal
        ]
        closed_related = [
            t for t in all_tickets
            if t.get("decision_id") == decision_id
            and t.get("id") != ticket_id
            and t.get("status") in terminal
        ]
        if related:
            lines.append(f"\nSame decision ({decision_id}) — open siblings:")
            for t in related[:5]:
                lines.append(f"  {t['id']} [{t.get('status','?')}] {t.get('title','')[:60]}")
        if closed_related:
            lines.append(f"Same decision — {len(closed_related)} closed sibling(s):")
            for t in closed_related[:3]:
                result_snippet = _truncate(t.get("result") or "", 80)
                lines.append(f"  {t['id']} [done] {t.get('title','')[:50]} — {result_snippet}")

    # ── File-proximity: tickets touching same affected files ───────────────────
    affected = _extract_affected_files(ticket.get("description", ""))
    if affected:
        file_related: dict[str, list[str]] = {}  # file → ticket ids
        for t in all_tickets:
            if t.get("id") == ticket_id:
                continue
            if t.get("status") in terminal and int(t.get("reset_count") or 0) == 0:
                continue  # skip clean closed tickets for brevity
            t_desc = t.get("description", "")
            for f in affected:
                # Match by filename stem (not full path, paths drift)
                stem = Path(f).name
                if stem and stem in t_desc:
                    file_related.setdefault(f, []).append(
                        f"{t['id']} [{t.get('status','?')}]"
                    )
        if file_related:
            lines.append("\nFile-proximity matches:")
            shown = 0
            for fname, tickets in list(file_related.items())[:3]:
                lines.append(f"  {fname}: {', '.join(tickets[:4])}")
                shown += 1
            if shown == 0:
                pass  # nothing to show

    if not lines:
        # No briefing to surface
        return

    print("─" * 60)
    print("PRE-BRIEF (anticipatory context)")
    print("─" * 60)
    for line in lines:
        print(line)
    print("─" * 60)


if __name__ == "__main__":
    main()
