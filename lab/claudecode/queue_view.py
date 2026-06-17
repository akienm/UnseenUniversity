#!/usr/bin/env python3
"""
Compiled queue views for /mytickets and /opentickets skills.

Replaces inline bash grep pipes in skill files with a single script call,
so the formatting logic is compiled once rather than re-derived each session.

Usage:
    queue_view.py --view mytickets      # tickets for Akien (guru/akien role/worker)
    queue_view.py --view opentickets    # all open tickets, grouped by status
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from collections import defaultdict
from pathlib import Path

_TERMINAL = {"done", "closed", "cancelled", "discarded"}

# Display order — canonical concept names per D-ticket-status-model-2026-06-16.
# Internal status strings are unchanged (sprint == READY) — this is the display
# layer only. Legacy values (approval/akien/pending/escalated) still render at
# the bottom until step 2 migrates them; design/open_questions/needs_review have
# folded into triage and no longer appear as their own group.
# Canonical status display vocab lives in unseen_university.ticket_status —
# imported (not copied) so a label change lands in every renderer at once.
#
# This script is invoked as a bare-file path by the /mytickets + /opentickets
# skills (`python3 ${CC_WORKFLOW_TOOLS}/queue_view.py`) under the SYSTEM python3,
# not the project venv. For a script file, sys.path[0] is the script's own dir
# (lab/claudecode), so `unseen_university` is NOT importable without help and the
# import below would raise ModuleNotFoundError. Put the repo root (two parents up)
# on sys.path first so the canonical-source import resolves under any interpreter.
_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from unseen_university.ticket_status import STATUS_LABEL as _STATUS_LABEL  # noqa: E402
from unseen_university.ticket_status import STATUS_ORDER as _STATUS_ORDER  # noqa: E402

_SIZE_ORDER = {"S": 0, "M": 1, "L": 2, "XL": 3}


def _gate_clear(gate_val: str | None, all_tickets: list) -> bool:
    """Inline gate check — mirrors cc_queue._gate_clear without import."""
    import re as _re
    from datetime import date as _date

    if not gate_val:
        return True
    first_token = gate_val.split()[0] if gate_val.strip() else ""
    if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", first_token):
        try:
            return _date.fromisoformat(first_token) <= _date.today()
        except ValueError:
            pass
    for t in all_tickets:
        if t["id"] in gate_val:
            return t.get("status") in _TERMINAL
    return False


def _effective_status(t: dict, all_tickets: list) -> str:
    """Return display status — reclassifies gated sprint tickets as 'dependency'."""
    status = t.get("status", "unknown")
    if status == "sprint" and t.get("gate") and not _gate_clear(t.get("gate"), all_tickets):
        return "dependency"
    return status


def _gate_label(t: dict) -> str:
    gate = t.get("gate") or ""
    return f" [gate: {gate}]" if gate else ""


def _load_tickets() -> list[dict]:
    """Load all tickets by importing cc_queue._load() directly."""
    cc_tools = os.environ.get(
        "CC_WORKFLOW_TOOLS",
        str(Path.home() / "dev/src/UnseenUniversity/lab/claudecode"),
    )
    if cc_tools not in sys.path:
        sys.path.insert(0, cc_tools)
    import importlib
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "cc_queue", str(Path(cc_tools) / "cc_queue.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._load()


def _is_open(t: dict) -> bool:
    return t.get("status", "") not in _TERMINAL


def _title_clean(t: dict) -> str:
    title = t.get("title", "")
    # Strip status prefix e.g. "[in_progress] Title"
    if title.startswith("[") and "]" in title:
        title = title.split("]", 1)[1].strip()
    return title


def _size(t: dict) -> str:
    return t.get("size", "?")


def view_mytickets(tickets: list[dict]) -> None:
    """Show tickets assigned to Akien (role=guru or worker=akien)."""
    mine = [
        t for t in tickets
        if _is_open(t) and (
            t.get("role") in ("guru", "akien") or
            t.get("worker") in ("akien", "guru")
        )
    ]

    if not mine:
        print("No tickets assigned to Akien right now.")
        return

    by_status: dict[str, list[dict]] = defaultdict(list)
    for t in mine:
        by_status[_effective_status(t, tickets)].append(t)

    print("MY TICKETS — Akien (guru role)")
    for status in _STATUS_ORDER:
        group = by_status.get(status, [])
        if not group:
            continue
        group.sort(key=lambda t: (
            -float(t.get("priority", 0.5)),
            _SIZE_ORDER.get(_size(t), 9),
        ))
        label = _STATUS_LABEL.get(status, status.title())
        print(f"\n{label}:")
        for t in group:
            gh = f" GH#{t['github_issue']}" if t.get("github_issue") else ""
            print(f"  {t['id']:40s} ({_size(t)}){gh}  {_title_clean(t)}")


def view_opentickets(tickets: list[dict]) -> None:
    """Show all open tickets grouped by status with totals."""
    open_tickets = [t for t in tickets if _is_open(t)]

    by_status: dict[str, list[dict]] = defaultdict(list)
    for t in open_tickets:
        by_status[_effective_status(t, tickets)].append(t)

    counts: dict[str, int] = {}

    for status in _STATUS_ORDER:
        group = by_status.get(status, [])
        counts[status] = len(group)
        if not group:
            continue
        if status == "hold":
            # Summarise OR-exhausted holds as count only
            or_exhausted = [
                t for t in group
                if "OR" in (t.get("result") or "") or "exhausted" in (t.get("result") or "").lower()
            ]
            real_holds = [t for t in group if t not in or_exhausted]
            if or_exhausted:
                print(f"\nHold (OR-exhausted, {len(or_exhausted)} suppressed):")
            if real_holds:
                print(f"\nHold (blocked):")
                real_holds.sort(key=lambda t: -float(t.get("priority", 0.5)))
                for t in real_holds[:10]:
                    print(f"  {t['id']:40s} ({_size(t)})  {_title_clean(t)}")
            continue

        label = _STATUS_LABEL.get(status, status.title())
        group.sort(key=lambda t: (
            -float(t.get("priority", 0.5)),
            _SIZE_ORDER.get(_size(t), 9),
        ))
        limit = 5 if status == "triage" else 20
        print(f"\n{label} ({len(group)}):")
        for t in group[:limit]:
            role = t.get("role", "")
            role_tag = f" [{role}]" if role else ""
            gate_tag = _gate_label(t)
            print(f"  {t['id']:40s} ({_size(t)}){role_tag}{gate_tag}  {_title_clean(t)}")
        if len(group) > limit:
            print(f"  … and {len(group) - limit} more")

    totals = " · ".join(
        f"{counts.get(s, 0)} {s}" for s in ["in_progress", "sprint", "hold", "triage"]
        if counts.get(s, 0) > 0
    )
    print(f"\nTotals: {totals}")
    print("Web queue: http://localhost:8082/queue")


def main() -> None:
    parser = argparse.ArgumentParser(description="Queue views for CC skills")
    parser.add_argument(
        "--view",
        choices=["mytickets", "opentickets"],
        required=True,
        help="Which view to render",
    )
    args = parser.parse_args()

    tickets = _load_tickets()

    if args.view == "mytickets":
        view_mytickets(tickets)
    else:
        view_opentickets(tickets)


if __name__ == "__main__":
    main()
