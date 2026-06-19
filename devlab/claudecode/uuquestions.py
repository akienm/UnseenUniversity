#!/usr/bin/env python3
"""uuquestions — surface TRIAGE tickets, flagging those with open questions.

Per D-ticket-status-model-2026-06-16, design and open_questions folded into
TRIAGE. A ticket "has open questions" when its description carries a Q<n>: line
with no matching A<n>: answer — that's a property of the description now, not a
separate status. This tool surfaces TRIAGE tickets and separates the ones still
waiting on Akien's answers from the rest.

Usage: uuquestions [--questions-only | --design-only]
  --questions-only  only TRIAGE tickets with unanswered questions
  --design-only     only TRIAGE tickets without unanswered questions
"""
from __future__ import annotations

import argparse
import re
import sys

from unseen_university import ticket_store


def _open_questions(description: str) -> list[str]:
    """Return Q<n>: lines that have no matching A<n>: answer."""
    if not description:
        return []
    answered = set(re.findall(r'A(\d+):', description))
    out = []
    for m in re.finditer(r'Q(\d+):\s*(.+)', description):
        if m.group(1) not in answered:
            out.append(f"Q{m.group(1)}: {m.group(2).strip()}")
    return out


def main(argv=None):
    p = argparse.ArgumentParser(prog="uuquestions", add_help=False)
    p.add_argument("--design-only", action="store_true")
    p.add_argument("--questions-only", action="store_true")
    args = p.parse_args(argv)

    rows = ticket_store.list(status_filter="triage")
    rows.sort(key=lambda t: (-(float(t.get("priority") or 0)), t.get("id") or ""))

    # Split TRIAGE into "has open questions" vs "no open questions".
    with_q: list[tuple[dict, list[str]]] = []
    without_q: list[dict] = []
    for t in rows:
        oq = _open_questions(t.get("description", ""))
        (with_q.append((t, oq)) if oq else without_q.append(t))

    def _title(t: dict) -> str:
        title = t.get("title", "")
        if title.startswith("[") and "]" in title:
            title = title[title.index("]") + 1:].strip()
        return title

    printed = False
    if not args.design_only and with_q:
        print("OPEN QUESTIONS (triage, awaiting answers):")
        for t, oq in with_q:
            print(f"  ❓ {t.get('id','?')} ({t.get('size','?')}) — {_title(t)}")
            print(f"       {oq[0]}")
        printed = True

    if not args.questions_only and without_q:
        if printed:
            print()
        print("TRIAGE (needs classification / design):")
        for t in without_q:
            print(f"  🔍 {t.get('id','?')} ({t.get('size','?')}) — {_title(t)}")
        printed = True

    if not printed:
        print("(none)")
        sys.exit(0)


if __name__ == "__main__":
    main(sys.argv[1:])
