#!/usr/bin/env python3
"""consequence_aging — surface consequence tickets whose gate has come due but sit unworked.

A consequence gate mandates the ticket's EXISTENCE (audit-ticket #19) but nothing
makes the ticket FIRE (gate-attack G7): many T-consequence-* tickets from June sat
open in sprint past their gate dates. The loop that makes decisions falsifiable
closes on paper, not in reality. This zero-inference check runs at day-close: it
lists consequence tickets whose gate has cleared (its date elapsed and any
predecessors terminal) while the ticket is still non-terminal, with age since due,
and escalates the >=7-day ones to Akien's inbox.

Calm signals (a list, not urgency flags) — surfacing only; it never auto-works or
auto-closes a consequence ticket.

Run: python3 devlab/claudecode/consequence_aging.py
Prints ``consequence overdue: N (oldest: T-..., age Xd)`` even when N is 0.
"""
from __future__ import annotations

import os
import sys
from datetime import date as _date

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _REPO)

from unseen_university.gate_logic import (  # noqa: E402
    GATE_DATE_RE,
    TERMINAL_STATUSES,
    gate_clear,
)

CONSEQUENCE_PREFIX = "T-consequence-"
ESCALATE_AGE_DAYS = 7


def _due_date(gate_val: str) -> "_date | None":
    """The date this gate became due = the LATEST date token (a gate is clear only
    once EVERY date has elapsed, mirroring gate_logic). None if no valid date."""
    latest = None
    for tok in GATE_DATE_RE.findall(gate_val or ""):
        try:
            d = _date.fromisoformat(tok)
        except ValueError:
            continue
        if latest is None or d > latest:
            latest = d
    return latest


def overdue_consequences(tasks: list, today: "_date | None" = None) -> list:
    """Return overdue consequence tickets, most-overdue first.

    Overdue = id starts T-consequence-, status non-terminal, gate fully CLEAR
    (all dates elapsed AND all id-predecessors terminal), and the due date is
    strictly in the past. Each entry: {id, due, age_days, gate}.
    """
    today = today or _date.today()
    out = []
    for t in tasks:
        tid = t.get("id") or ""
        if not tid.startswith(CONSEQUENCE_PREFIX):
            continue
        if t.get("status") in TERMINAL_STATUSES:
            continue
        gate = t.get("gate") or ""
        due = _due_date(gate)
        if due is None:
            continue  # no date component — not a date-gated consequence
        if due >= today:
            continue  # not yet due (or due today — not overdue yet)
        # Fully clear? (predecessors terminal too — else it's legitimately blocked)
        if not gate_clear(gate, tasks):
            continue
        out.append({
            "id": tid,
            "due": due.isoformat(),
            "age_days": (today - due).days,
            "gate": gate,
        })
    out.sort(key=lambda e: e["age_days"], reverse=True)
    return out


def format_summary(overdue: list) -> str:
    if not overdue:
        return "consequence overdue: 0"
    oldest = overdue[0]
    return (f"consequence overdue: {len(overdue)} "
            f"(oldest: {oldest['id']}, age {oldest['age_days']}d)")


def main() -> int:
    from unseen_university import ticket_store
    tasks = ticket_store.list()
    overdue = overdue_consequences(tasks)
    print(format_summary(overdue))
    for e in overdue:
        mark = "  ⚠ ESCALATE" if e["age_days"] >= ESCALATE_AGE_DAYS else ""
        print(f"  {e['id']}  due {e['due']}  age {e['age_days']}d{mark}")
    # Non-zero exit only signals presence for a caller that wants to branch;
    # day-close treats this as informational (calm signal), never a hard stop.
    return 0


if __name__ == "__main__":
    sys.exit(main())
