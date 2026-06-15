#!/usr/bin/env python3
"""
cc_queue.py — Designer/Worker Claude task queue manager.

Canonical storage: clan.memories where parent_id='TICKETS_ROOT' (FACTUAL rows,
metadata.kind='ticket').

Log file:  ~/.unseen_university/cc_channel/log.jsonl

Statuses (what happens next):
    triage      — needs classification; any agent can triage
    design      — needs design work before sprinting
    open_questions — has numbered Q1:/Q2: questions without matching A1:/A2: answers; Akien answers, status flips to sprint
    approval    — plan submitted, awaiting Akien sign-off
    akien       — requires Akien to take an external action
    sprint      — ready to pick up and work
    in_progress — assigned, actively in flight
    hold        — explicitly paused (reason in ticket)
    dependency  — gated on a future event or condition
    pending     — waiting on a specific other ticket (list it)
    cancelled   — decided not to do
    closed      — done

Usage:
    cc_queue.py list                          — show tasks (sprint first, gated hidden)
    cc_queue.py list --gated                  — include gated tickets in the list
    cc_queue.py list --by-decision            — group output by decision_id
    cc_queue.py add <json-file>               — add task from JSON file (defaults to triage)
    cc_queue.py done <id> <msg>               — mark task awaiting_validation (Igor's submit path)
    cc_queue.py close <id> <msg>              — mark task closed (CC's validated-close path)
    cc_queue.py block <id> <msg>              — mark task hold with reason
    cc_queue.py setstatus <id> <status>       — set any status directly
    cc_queue.py show <id>                     — show full task detail
    cc_queue.py log <msg>                     — append a free-form log entry
    cc_queue.py flush_decision <id> <summary> — flush decision to Igor memory
    cc_queue.py flush_session <session> <summary> — flush session blob to Igor memory
    cc_queue.py next --worker <name> [--max-difficulty=N]  — mark in_progress + return highest-priority sprint ticket for worker; errors if --worker omitted
    cc_queue.py worker-launch                     — ensure worker daemon is running (spawns konsole if not)
    cc_queue.py reset [--timeout] <id>           — reset one ticket from in_progress → sprint; --timeout increments counter, trips gate at 3
    cc_queue.py reset-stale                       — reset all in_progress tickets → sprint (daemon startup cleanup)
    cc_queue.py set-worker <worker> <id> [<id>]  — assign worker (igor|claude) to ticket(s)
    cc_queue.py needs-review <id>                — mark ticket triage (review gate)
    cc_queue.py gate <id> <reason>               — gate a ticket behind a precondition (hides from default list)
    cc_queue.py ungate <id> [note]               — clear a ticket's gate
    cc_queue.py set-decision <id> <decision-id>  — attach a decision id to a ticket
    cc_queue.py migrate-statuses                 — one-time migration: strip title prefixes, map old → new statuses
"""

import json
import os
import ssl
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

IGOR_FLUSH_URL = "https://localhost:8080/api/cc_send"

TICKETS_ROOT_ID = "TICKETS_ROOT"


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


LOG_PATH = os.path.expanduser("~/.unseen_university/cc_channel/log.jsonl")
CLOSED_TICKETS_PATH = os.path.expanduser(
    "~/.unseen_university/claudecode/closed_tickets.txt"
)
GATE_FILE = os.path.expanduser("~/.unseen_university/cc_channel/queue_gate.json")

DIFFICULTY_TIERS = {
    1: "Apprentice",
    2: "Sustainer",
    3: "Creator",
    4: "Master",
    5: "Teacher",
}

# Role ladder: minimum capability level required to execute a ticket.
# apprentice → OR cascade ok; builder+ → needs CC.0 or DickSimnel.0.
VALID_ROLES = {"apprentice", "builder", "creator", "master", "guru"}

# Backward-compat inference when role field is absent.
_WORKER_TO_ROLE = {
    "claude": "master",
    "cc": "master",
    "dicksimnel": "builder",
    "igor": "apprentice",
}

STATUS_ORDER = {
    # Canonical statuses (what happens next):
    "triage": 0,
    "design": 1,
    "open_questions": 1.5,
    "approval": 2,
    "akien": 3,
    "sprint": 4,
    "dispatched": 4.5,
    "acked": 4.75,
    "in_progress": 5,
    "awaiting_validation": 6,
    "escalated": 6.5,  # failed at current tier — awaiting higher-tier pickup
    "hold": 7,
    "dependency": 8,
    "pending": 9,
    "cancelled": 10,
    "closed": 11,
    # Legacy aliases (kept for old DB rows):
    "needs_review": 0,
    "awaiting_approval": 2,
    "blocked": 7,
    "done": 11,
}

_TERMINAL_STATUSES = {"closed", "done", "cancelled"}
_ACTIONABLE_STATUSES = {"sprint", "design", "akien", "awaiting_approval", "approval"}

# Status prefix helpers — embed [status] in title for one-grep searchability
_STATUS_PREFIX_RE = None


def _strip_status_prefix(title: str) -> str:
    """Remove a leading [status] token if present."""
    import re

    return re.sub(r"^\[[a-z_]+\]\s*", "", title)


def _with_status_prefix(status: str, title: str) -> str:
    """Return title with [status] prepended, stripping any prior prefix."""
    bare = _strip_status_prefix(title)
    if status in _TERMINAL_STATUSES:
        return bare
    return f"[{status}] {bare}"


def _db_conn():
    """Connect to clan.memories storage."""
    import psycopg2

    url = os.environ.get("UU_HOME_DB_URL") or os.environ.get("IGOR_HOME_DB_URL")
    if not url:
        raise RuntimeError("UU_HOME_DB_URL not set")
    return psycopg2.connect(url)


def _narrative_for(t: dict) -> str:
    """Narrative = title + description (both GIN-searchable)."""
    title = (t.get("title") or "").strip()
    desc = (t.get("description") or t.get("body") or "").strip()
    return f"{title}\n\n{desc}" if desc else title


def _tickets_in_clan(ticket_ids: list[str]) -> set:
    """Return set of ticket IDs that exist in clan.memories."""
    if not ticket_ids:
        return set()
    conn = _db_conn()
    try:
        cur = conn.cursor()
        placeholders = ",".join(["%s"] * len(ticket_ids))
        cur.execute(
            f"SELECT id FROM clan.memories WHERE id IN ({placeholders}) AND parent_id = %s",
            ticket_ids + [TICKETS_ROOT_ID],
        )
        return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def _load():
    """Canonical read: SELECT from clan.memories and devlab.tickets (merged).

    During transition, read from both tables:
    - clan.memories: existing tickets (old format with metadata JSONB)
    - devlab.tickets: new tickets (new format with explicit columns)

    Merge results, preferring devlab if a ticket appears in both.
    """
    conn = _db_conn()
    try:
        cur = conn.cursor()
        tasks = {}

        # Read from clan.memories (existing tickets)
        cur.execute(
            "SELECT metadata FROM clan.memories WHERE parent_id = %s",
            (TICKETS_ROOT_ID,),
        )
        for (md,) in cur.fetchall():
            if not md:
                continue
            t = dict(md)
            t.pop("kind", None)
            tasks[t.get("id")] = t

        # Read from devlab.tickets (new tickets) — these override clan if present
        cur.execute(
            """SELECT id, title, status, worker, size, tags, description,
                      decision_id, metadata, created_at, updated_at, completed_at
               FROM devlab.tickets
               ORDER BY created_at DESC"""
        )
        for row in cur.fetchall():
            (ticket_id, title, status, worker, size, tags, description,
             decision_id, metadata, created_at, updated_at, completed_at) = row

            t = {
                "id": ticket_id,
                "title": title,
                "status": status,
                "worker": worker,
                "size": size,
                "tags": tags or [],
                "description": description,
                "decision_id": decision_id,
                "created_at": created_at.isoformat() if created_at else None,
                "updated_at": updated_at.isoformat() if updated_at else None,
                "completed_at": completed_at.isoformat() if completed_at else None,
                "result": None,
                "role": "master",  # default role
                "priority": 0.5,  # default priority
                "gate": None,
                "related_to": None,
                "github_issue": None,
                "dispatched_at": None,
                "required_files": [],
                "target_difficulty": 1,
            }

            # Merge metadata fields if present
            if metadata:
                t.update(metadata)

            tasks[ticket_id] = t

        return list(tasks.values())
    finally:
        conn.close()


def _save(tasks):
    """Canonical write: UPSERT each ticket.

    Strategy during transition:
    - NEW tickets: write to devlab.tickets
    - EXISTING tickets (in clan): update clan.memories in-place
    """
    if not tasks:
        return

    conn = _db_conn()
    try:
        cur = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        now_ts = datetime.now(timezone.utc)

        # Batch-check which tickets exist in clan
        ticket_ids = [t.get("id") for t in tasks if t.get("id")]
        existing_in_clan = _tickets_in_clan(ticket_ids)

        for t in tasks:
            if not t.get("id"):
                continue

            ticket_id = t["id"]
            exists_in_clan = ticket_id in existing_in_clan

            if exists_in_clan:
                # Update existing clan ticket in-place
                metadata = dict(t)
                metadata["kind"] = "ticket"
                cur.execute(
                    """
                    INSERT INTO clan.memories
                      (id, narrative, memory_type, parent_id, metadata, timestamp,
                       source, scope, certainty, updated_at)
                    VALUES (%s, %s, 'FACTUAL', %s, %s::jsonb, %s, 'cc_queue',
                            'class', 1.0, %s)
                    ON CONFLICT (id) DO UPDATE SET
                      narrative = EXCLUDED.narrative,
                      metadata = EXCLUDED.metadata,
                      updated_at = EXCLUDED.updated_at
                    """,
                    (
                        ticket_id,
                        _narrative_for(t),
                        TICKETS_ROOT_ID,
                        json.dumps(metadata),
                        now,
                        now,
                    ),
                )
            else:
                # Write new ticket to devlab.tickets
                cur.execute(
                    """
                    INSERT INTO devlab.tickets
                      (id, title, status, worker, size, tags, description,
                       decision_id, metadata, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                      title = EXCLUDED.title,
                      status = EXCLUDED.status,
                      worker = EXCLUDED.worker,
                      size = EXCLUDED.size,
                      tags = EXCLUDED.tags,
                      description = EXCLUDED.description,
                      decision_id = EXCLUDED.decision_id,
                      metadata = EXCLUDED.metadata,
                      updated_at = EXCLUDED.updated_at,
                      completed_at = EXCLUDED.completed_at
                    """,
                    (
                        ticket_id,
                        t.get("title"),
                        t.get("status", "triage"),
                        t.get("worker"),
                        t.get("size"),
                        json.dumps(t.get("tags", [])),
                        t.get("description"),
                        t.get("decision_id"),
                        json.dumps({k: v for k, v in t.items()
                                   if k not in ("id", "title", "status", "worker", "size",
                                               "tags", "description", "decision_id")}),
                        now_ts,
                        now_ts,
                    ),
                )

        conn.commit()
    finally:
        conn.close()


# ── Public API ────────────────────────────────────────────────────────────


def load_tasks() -> list[dict]:
    """Load all tickets from canonical Postgres."""
    return _load()


def save_tasks(tasks: list[dict]) -> None:
    """Save tickets via canonical Postgres UPSERT."""
    _save(tasks)


def set_status_in_progress(ticket_id: str) -> bool:
    """Targeted single-ticket status flip sprint→in_progress. Returns True if updated.

    Updates whichever table the ticket is in (devlab.tickets or clan.memories).
    """
    conn = _db_conn()
    try:
        cur = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        now_ts = datetime.now(timezone.utc)

        # Try updating devlab.tickets first (new tickets)
        cur.execute(
            """
            UPDATE devlab.tickets
            SET status = 'in_progress',
                updated_at = %s
            WHERE id = %s
              AND status = 'sprint'
            """,
            (now_ts, ticket_id),
        )
        updated = cur.rowcount > 0

        # If not found in devlab, try clan.memories (existing tickets)
        if not updated:
            cur.execute(
                """
                UPDATE clan.memories
                SET metadata = jsonb_set(
                        jsonb_set(metadata, '{status}', '"in_progress"'),
                        '{dispatched_at}', to_jsonb(%s::text)
                    ),
                    updated_at = %s
                WHERE id = %s
                  AND metadata->>'status' = 'sprint'
                  AND parent_id = %s
                """,
                (now, now, ticket_id, TICKETS_ROOT_ID),
            )
            updated = cur.rowcount > 0

        conn.commit()
        if updated:
            import logging

            logging.getLogger(__name__).info(
                f"QUEUE_DRAIN: reconciled {ticket_id} → in_progress"
            )
        return updated
    finally:
        conn.close()


def reset_stale_in_progress(ticket_id: str) -> bool:
    """Reset a stale in_progress ticket to sprint, ONLY if still in_progress in DB.

    Race-safe: the WHERE clause gates on current DB status, so a concurrent
    setstatus/close that already made the ticket terminal is never overwritten.
    Returns True if the reset happened, False if the ticket was already terminal.
    """
    conn = _db_conn()
    try:
        cur = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        cur.execute(
            """
            UPDATE clan.memories
            SET metadata = jsonb_set(
                    metadata #- '{dispatched_at}',
                    '{status}', '"sprint"'
                ),
                updated_at = %s
            WHERE id = %s
              AND metadata->>'status' = 'in_progress'
              AND parent_id = %s
            """,
            (now, ticket_id, TICKETS_ROOT_ID),
        )
        updated = cur.rowcount > 0
        conn.commit()
        if updated:
            import logging

            logging.getLogger(__name__).info(
                f"QUEUE_DRAIN: reset stale dispatch {ticket_id} → sprint"
            )
        return updated
    finally:
        conn.close()


def _log(entry: dict):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    entry["ts"] = datetime.now(timezone.utc).isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _prepend_closed_ticket(tid: str, title: str) -> None:
    """Prepend one line to closed_tickets.txt (newest at top)."""
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    line = f"{date_str} | {tid} | {title}\n"
    os.makedirs(os.path.dirname(CLOSED_TICKETS_PATH), exist_ok=True)
    existing = ""
    if os.path.exists(CLOSED_TICKETS_PATH):
        with open(CLOSED_TICKETS_PATH) as f:
            existing = f.read()
    with open(CLOSED_TICKETS_PATH, "w") as f:
        f.write(line + existing)


def _find(tasks, tid):
    for t in tasks:
        if t["id"] == tid:
            return t
    return None


def _format_task_line(t: dict) -> str:
    STATUS_ICON = {
        "triage": "🔍",
        "design": "📐",
        "open_questions": "❓",
        "approval": "🟠",
        "akien": "👤",
        "sprint": "⬜",
        "in_progress": "🔵",
        "awaiting_validation": "🔶",
        "hold": "⏸",
        "dependency": "🔗",
        "pending": "⏳",
        "cancelled": "❌",
        "closed": "✅",
        # Legacy:
        "needs_review": "🟡",
        "awaiting_approval": "🟠",
        "blocked": "🔴",
        "done": "✅",
    }
    icon = STATUS_ICON.get(t["status"], "?")
    size = t.get("size", "?")
    epic = f" #{t['epic']}" if t.get("epic") else ""
    worker_tag = " [igor]" if t.get("worker") == "igor" else ""
    created_by_tag = f" [{t.get('created_by') or 'unknown'}]"
    gh_tag = f" GH#{t['github_issue']}" if t.get("github_issue") else ""
    diff = t.get("target_difficulty", 1)
    tier_tag = f" [{DIFFICULTY_TIERS.get(diff, '?')}({diff})]" if diff != 1 else ""
    role = _infer_role(t)
    role_tag = f" [{role}]" if role and role != "apprentice" else ""
    cost_usd = t.get("cost_usd")
    cost_tag = f" ${cost_usd:.2f}" if cost_usd is not None and t.get("status") in ("closed", "done", "awaiting_validation") else ""
    return f"  {icon} [{t['id']}] ({size}){epic}{worker_tag}{created_by_tag}{gh_tag}{tier_tag}{role_tag} {t['title']}  [{t['status']}]{cost_tag}"


def _print_task(t: dict) -> None:
    print(_format_task_line(t))
    if t["status"] in ("blocked", "hold") and t.get("result"):
        print(f"       HOLD: {t['result']}")
    if t["status"] in ("done", "awaiting_validation", "closed") and t.get("result"):
        print(f"       done: {t['result']}")


def cmd_list(args):
    by_epic = "--by-epic" in args
    show_gated = "--gated" in args
    by_decision = "--by-decision" in args
    actionable = "--actionable" in args
    tasks = _load()
    if not tasks:
        print("Queue empty.")
        return

    if actionable:
        tasks = [
            t
            for t in tasks
            if t.get("status") in _ACTIONABLE_STATUSES
            and t.get("worker") != "igor"
            and _gate_clear(t.get("gate"), tasks)
        ]
    elif not show_gated:
        tasks = [t for t in tasks if not t.get("gate")]

    def _priority_int(t):
        p = t.get("priority", 99)
        try:
            return int(str(p).lstrip("pP"))
        except (ValueError, TypeError):
            return 99

    tasks_sorted = sorted(
        tasks, key=lambda t: (STATUS_ORDER.get(t["status"], 9), _priority_int(t))
    )

    if by_epic:
        from collections import defaultdict

        groups: dict[str, list] = defaultdict(list)
        for t in tasks_sorted:
            groups[t.get("epic") or "(no epic)"].append(t)
        for epic_name in sorted(groups):
            print(f"\n## #{epic_name}")
            for t in groups[epic_name]:
                _print_task(t)
    elif by_decision:
        from collections import defaultdict

        groups: dict[str, list] = defaultdict(list)
        for t in tasks_sorted:
            groups[t.get("decision_id") or "(no decision)"].append(t)
        for decision in sorted(groups):
            print(f"\n## {decision}")
            for t in groups[decision]:
                _print_task(t)
    else:
        for t in tasks_sorted:
            _print_task(t)


def cmd_show(args):
    if not args:
        print("Usage: show <id>")
        sys.exit(1)
    tasks = _load()
    t = _find(tasks, args[0])
    if not t:
        print(f"Task {args[0]} not found.")
        sys.exit(1)
    print(json.dumps(t, indent=2))
    _show_token_log(args[0])


# ---------------------------------------------------------------------------
# Model pricing table (USD per million tokens, as of 2026-06)
# Keys are model name substrings matched case-insensitively.
# ---------------------------------------------------------------------------
_MODEL_PRICING: dict[str, dict[str, float]] = {
    # claude-sonnet-4-5 / claude-sonnet-4-6
    "sonnet": {
        "input": 3.00,
        "cache_write": 3.75,
        "cache_read": 0.30,
        "output": 15.00,
    },
    # claude-haiku-3-5
    "haiku": {
        "input": 0.80,
        "cache_write": 1.00,
        "cache_read": 0.08,
        "output": 4.00,
    },
    # claude-opus-4
    "opus": {
        "input": 15.00,
        "cache_write": 18.75,
        "cache_read": 1.50,
        "output": 75.00,
    },
}
_MODEL_PRICING_DEFAULT = _MODEL_PRICING["sonnet"]


def _pricing_for_model(model: str) -> dict[str, float]:
    """Return the pricing dict for a given model name string."""
    ml = model.lower()
    for key, pricing in _MODEL_PRICING.items():
        if key in ml:
            return pricing
    return _MODEL_PRICING_DEFAULT


def _compute_cost_usd(ticket_id: str) -> float | None:
    """Compute total inference cost in USD for a ticket from sprint_tokens.log.

    Returns None if no log entries exist for the ticket.
    """
    igor_home = Path(os.environ.get("IGOR_HOME", str(Path.home() / ".unseen_university")))
    log_path = igor_home / "claudecode" / "sprint_tokens.log"
    if not log_path.exists():
        return None
    entries = [
        ln for ln in log_path.read_text(encoding="utf-8").splitlines()
        if ln and len(ln.split("|")) >= 7 and ln.split("|")[1] == ticket_id
    ]
    if not entries:
        return None
    total_cost = 0.0
    for entry in entries:
        parts = entry.split("|")
        _ts, _tid, inp, cache_w, cache_r, out, model = parts[:7]
        pricing = _pricing_for_model(model.strip())
        cost = (
            int(inp) * pricing["input"]
            + int(cache_w) * pricing["cache_write"]
            + int(cache_r) * pricing["cache_read"]
            + int(out) * pricing["output"]
        ) / 1_000_000
        total_cost += cost
    return total_cost


def _show_token_log(ticket_id: str) -> None:
    """Append per-sprint token consumption from sprint_tokens.log, if any."""
    igor_home = Path(os.environ.get("IGOR_HOME", str(Path.home() / ".unseen_university")))
    log_path = igor_home / "claudecode" / "sprint_tokens.log"
    if not log_path.exists():
        return
    entries = [
        ln for ln in log_path.read_text(encoding="utf-8").splitlines()
        if ln and ln.split("|")[1] == ticket_id
    ]
    if not entries:
        return
    print("\nToken consumption (sprints):")
    total_cost = 0.0
    for entry in entries:
        parts = entry.split("|")
        if len(parts) < 7:
            continue
        ts, tid, inp, cache_w, cache_r, out, model = parts[:7]
        total_in = int(inp) + int(cache_w) + int(cache_r)
        pricing = _pricing_for_model(model.strip())
        cost = (
            int(inp) * pricing["input"]
            + int(cache_w) * pricing["cache_write"]
            + int(cache_r) * pricing["cache_read"]
            + int(out) * pricing["output"]
        ) / 1_000_000
        total_cost += cost
        print(
            f"  {ts[:19]}  in={total_in:>7} "
            f"(write={int(cache_w):>6} read={int(cache_r):>6})  "
            f"out={int(out):>6}  [{model.strip()}]  ${cost:.4f}"
        )
    if len(entries) > 1:
        print(f"  Total cost: ${total_cost:.4f}")


class LegacyDirectClaimError(Exception):
    """Raised unconditionally when any code tries to autonomously claim a ticket.

    Autonomous claiming is removed. Igor owns no ticket unless CC explicitly
    dispatches it via:
        cc_queue.py dispatch <ticket-id> [--by <name>]
    Workers must not pull from the queue on their own initiative.
    """





def cmd_dispatch(args):
    """Dispatch a ticket to a worker — the ONLY legitimate path for CC to assign work.

    Usage: dispatch <ticket-id> [--by <dispatcher>]

    Sets status=in_progress and records dispatched_by + dispatched_at.
    Igor must not call this himself — it is the CC→Igor handoff command.
    After dispatching, CC calls goal_adopt("work ticket <id>") via the Igor channel.

    Raises SystemExit(1) if ticket not found or not in sprint status.
    """
    if not args:
        print("Usage: dispatch <ticket-id> [--by <dispatcher>]", file=sys.stderr)
        sys.exit(1)

    dispatched_by = "cc"
    clean_args = list(args)
    if "--by" in clean_args:
        i = clean_args.index("--by")
        if i + 1 < len(clean_args):
            dispatched_by = clean_args[i + 1]
            del clean_args[i : i + 2]
        else:
            print("ERROR: --by requires a value.", file=sys.stderr)
            sys.exit(1)

    ticket_id = clean_args[0] if clean_args else None
    if not ticket_id:
        print("Usage: dispatch <ticket-id> [--by <dispatcher>]", file=sys.stderr)
        sys.exit(1)

    conn = _db_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT metadata FROM clan.memories WHERE id = %s FOR UPDATE",
            (ticket_id,),
        )
        row = cur.fetchone()
        if not row or not row[0]:
            print(f"Ticket {ticket_id} not found.", file=sys.stderr)
            conn.rollback()
            sys.exit(1)
        t = dict(row[0])
        t.pop("kind", None)
        if t.get("status") not in ("sprint", "in_progress"):
            print(
                f"Ticket {ticket_id} is not in sprint status (current: {t.get('status')}).",
                file=sys.stderr,
            )
            conn.rollback()
            sys.exit(1)
        now = _now()
        t["status"] = "in_progress"
        t["title"] = _with_status_prefix("in_progress", t["title"])
        t["dispatched_by"] = dispatched_by
        t["dispatched_at"] = now
        metadata = dict(t)
        metadata["kind"] = "ticket"
        cur.execute(
            """UPDATE clan.memories SET
                metadata = %s::jsonb,
                narrative = %s,
                updated_at = %s
            WHERE id = %s""",
            (json.dumps(metadata), _narrative_for(t), now, ticket_id),
        )
        conn.commit()
    except SystemExit:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _log({"action": "dispatch", "id": ticket_id, "dispatched_by": dispatched_by})
    _classifier_stamp_in_flight(ticket_id, t.get("required_files", []))
    print(f"dispatched {ticket_id} → {dispatched_by}")


def _classifier_stamp_in_flight(ticket_id: str, required_files: list) -> None:
    """Stamp palace.codebase nodes for required_files as in_flight. Non-fatal."""
    if not required_files:
        return
    try:
        from devices.classifier.device import ClassifierDevice
        ClassifierDevice(llm_fallback=False).stamp_in_flight(ticket_id, required_files)
    except Exception as exc:
        print(f"classifier stamp_in_flight: {exc}", file=sys.stderr)


def _classifier_clear_in_flight(ticket_id: str) -> None:
    """Clear palace.codebase in_flight flags for ticket_id. Non-fatal."""
    try:
        from devices.classifier.device import ClassifierDevice
        ClassifierDevice(llm_fallback=False).clear_in_flight(ticket_id)
    except Exception as exc:
        print(f"classifier clear_in_flight: {exc}", file=sys.stderr)


def _annotator_delta_update(ticket_id: str) -> None:
    """Re-annotate palace.codebase nodes for files touched in this ticket's commit. Non-fatal."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        if result.returncode != 0 or not result.stdout.strip():
            return
        touched = [f.strip() for f in result.stdout.splitlines() if f.strip()]
        from devices.classifier.annotator import run_annotator
        db_url = os.environ.get("UU_HOME_DB_URL") or os.environ.get(
            "IGOR_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
        )
        counts = run_annotator(db_url=db_url, file_paths=touched)
        print(
            f"annotator delta: ticket={ticket_id} files={len(touched)} "
            f"inserted={counts['inserted']} updated={counts['updated']} errors={counts['errors']}",
            file=sys.stderr,
        )
    except Exception as exc:
        print(f"annotator delta: {exc}", file=sys.stderr)


def _close_igor_goal(ticket_id: str) -> None:
    """Close Igor's GOAL memory for a ticket so pe_chain stops re-firing."""
    try:
        import psycopg2

        conn = psycopg2.connect(os.environ.get("UU_HOME_DB_URL") or os.environ["IGOR_HOME_DB_URL"])
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute(
            "UPDATE memories SET narrative = REPLACE(narrative, 'ACTIVE GOAL', 'CLOSED GOAL') "
            "WHERE memory_type='GOAL' AND narrative ILIKE %s AND narrative ILIKE '%%ACTIVE GOAL%%'",
            (f"%{ticket_id}%",),
        )
        closed = cur.rowcount
        conn.close()
        if closed:
            print(f"Closed {closed} GOAL(s) for {ticket_id}")
    except Exception as e:
        print(f"GOAL close failed (non-fatal): {e}")


def _decision_rollup(tasks: list, decision_id: str) -> None:
    """T-decision-rollup-on-last-ticket-close: when the last ticket of a decision
    closes, write a rollup doc + un-gate dependents referencing this decision.

    Preserves any pre-existing narrative. If the decision doc already exists
    with narrative content (i.e. doesn't start with the rollup header), the
    rollup block is APPENDED as a `## Rollup` section and the frontmatter
    `status: open` is flipped to `status: closed`. If the file is absent or
    already rollup-stub-shaped, the stub form is (re)written.

    Rollup location: lab/design_docs/decisions/<decision-id>.md (file-stub until
    T-decisions-into-palace-subtree moves this into the palace).
    """
    if not decision_id:
        return
    siblings = [t for t in tasks if t.get("decision_id") == decision_id]
    if not siblings:
        return
    open_count = sum(
        1 for t in siblings if t.get("status") not in ("done", "discarded", "blocked")
    )
    if open_count > 0:
        return

    # All tickets in this decision are closed. Roll up.
    from pathlib import Path
    import os as _os

    rollup_dir = Path(
        _os.path.expanduser("~/dev/src/UnseenUniversity/lab/design_docs/decisions")
    )
    rollup_dir.mkdir(parents=True, exist_ok=True)
    rollup_path = rollup_dir / f"{decision_id}.md"
    now = _now()
    closed_tickets = sorted(siblings, key=lambda t: t.get("completed_at") or "")

    rollup_lines = [
        f"**Closed at:** {now}",
        f"**Ticket count:** {len(siblings)} (all closed)",
        "",
        "### Shipped via",
    ]
    for t in closed_tickets:
        rollup_lines.append(
            f"- {t['id']} ({t.get('size', '?')}) — {t.get('title', '?')}  "
            f"`{t.get('status')}` — {(t.get('result') or '')[:200]}"
        )
    rollup_lines.append("")
    rollup_lines.append(
        "_Generated by cc_queue.py _decision_rollup. File-stub until "
        "T-decisions-into-palace-subtree moves rollups into the memory palace._"
    )
    rollup_block = "\n".join(rollup_lines)

    existing = rollup_path.read_text() if rollup_path.exists() else ""
    has_narrative = bool(existing) and not existing.lstrip().startswith(
        "# Decision rollup —"
    )

    if has_narrative:
        preserved = existing
        if "\nstatus: open" in preserved:
            preserved = preserved.replace("\nstatus: open", "\nstatus: closed", 1)
        final = preserved.rstrip() + "\n\n## Rollup\n\n" + rollup_block + "\n"
    else:
        final = f"# Decision rollup — {decision_id}\n\n" + rollup_block + "\n"

    rollup_path.write_text(final)
    shape = "narrative+rollup" if has_narrative else "stub"
    print(
        f"  [rollup] {decision_id} closed — {len(siblings)} tickets "
        f"({shape}). → {rollup_path}"
    )

    # Un-gate dependents whose gate text mentions this decision
    ungated = 0
    for t in tasks:
        gate = t.get("gate") or ""
        if not gate:
            continue
        if decision_id in gate:
            prev = t["gate"]
            t["gate"] = None
            ungated += 1
            print(f"  [rollup] ungated {t['id']} (was: {prev[:60]}...)")
    if ungated:
        _log(
            {
                "action": "decision_rollup_ungate",
                "decision_id": decision_id,
                "ungated_count": ungated,
            }
        )


def _append_to_todays_slate(ticket: dict) -> None:
    """T-sync-on-close-not-dayend: append closed ticket to today's slate done section.
    Idempotent. Handles both JSON and markdown slate formats.
    Graceful degrade: silent on missing slate or read/write failure.
    """
    try:
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        slate_path = os.path.expanduser(
            f"~/.unseen_university/claudecode/{today}.slate.txt"
        )
        if not os.path.exists(slate_path):
            return
        tid = ticket["id"]
        title = ticket.get("title", "")
        result = (ticket.get("result") or "").split("\n")[0][:120]
        entry = f"{tid} — {title}"
        if result:
            entry += f" ({result})"

        # Try JSON format first
        try:
            import json as _json
            with open(slate_path) as f:
                data = _json.load(f)
            done = data.get("done") or []
            if not any(tid in item for item in done):
                done.append(entry)
                data["done"] = done
                with open(slate_path, "w") as f:
                    _json.dump(data, f, indent=2)
            return
        except (ValueError, KeyError):
            pass

        # Markdown slate (old format)
        with open(slate_path) as f:
            content = f.read()
        bullet = f"- {entry}"
        lines = content.splitlines(keepends=True)
        out = []
        appended = False
        in_done = False
        for line in lines:
            if in_done and tid in line and line.lstrip().startswith("-"):
                appended = True
                out.append(line)
                continue
            out.append(line)
            if line.startswith("## Done"):
                in_done = True
                continue
            if in_done and line.startswith("## ") and not appended:
                out.insert(len(out) - 1, bullet + "\n")
                appended = True
                in_done = False
        if in_done and not appended:
            if out and not out[-1].endswith("\n"):
                out.append("\n")
            out.append(bullet + "\n")
            appended = True
        if appended:
            with open(slate_path, "w") as f:
                f.writelines(out)
    except Exception as e:
        _log({"action": "slate_append_failed", "error": str(e), "id": ticket.get("id")})


def _gate_clear(gate_val: str | None, all_tasks: list) -> bool:
    """Return True if gate is null, a past/today date, or a closed ticket reference.

    Priority:
    1. Null gate → clear.
    2. First token matches YYYY-MM-DD → date gate; clear only if date <= today.
    3. Any ticket ID found in the string → check that ticket's terminal status.
    4. Unknown format → fail closed (blocked).
    """
    import re as _re
    from datetime import date as _date
    import logging as _logging

    if not gate_val:
        return True

    # Date gate: first token is YYYY-MM-DD
    first_token = gate_val.split()[0] if gate_val.strip() else ""
    if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", first_token):
        try:
            gate_date = _date.fromisoformat(first_token)
            clear = gate_date <= _date.today()
            if not clear:
                _logging.getLogger(__name__).debug(
                    "[gate-date] blocked until %s (gate: %s)", gate_date, gate_val[:60]
                )
            return clear
        except ValueError:
            pass  # malformed date — fall through to fail-closed

    # Ticket-ID gate: scan for any known ticket id in the string
    for t in all_tasks:
        if t["id"] in gate_val:
            return t["status"] in _TERMINAL_STATUSES

    # Unknown format → fail closed
    return False


def _ungate_dependents(tasks: list, closed_id: str) -> int:
    """Clear `gate` on any pending task whose gate text references closed_id.

    Returns count of tickets ungated. Operates in-place; caller must _save.
    Mirrors the decision-rollup ungate pattern at the ticket-id level so
    gated chains (e.g. T-cc-walk-02 gated on T-cc-walk-01) flow on close.
    """
    ungated = 0
    for t in tasks:
        if t.get("status") in _TERMINAL_STATUSES:
            continue
        gate = t.get("gate") or ""
        if not gate:
            continue
        if closed_id in gate:
            t["gate"] = None
            ungated += 1
            print(f"  [ungate] {t['id']} (was gated on {closed_id})")
    if ungated:
        _log(
            {
                "action": "ungate_on_close",
                "closed_id": closed_id,
                "ungated_count": ungated,
            }
        )
    return ungated


_HIGH_INERTIA_PATTERNS = (
    "brainstem/",
    "memory/models.py",
    "cognition/reasoners/base.py",
)


def _try_auto_validate(tasks: list, t: dict) -> bool:
    """Auto-close a ticket if it meets all low-risk criteria.

    Criteria (ALL must pass):
    1. Size is S or M
    2. Description does not reference HIGH-inertia files
    3. Result message suggests tests passed (contains "pass", no "fail")
    4. Result does not indicate a SCOPE_GUARD trip
    5. Worker is igor (CC-side work is already validated at commit time)

    Returns True and transitions to closed when all criteria pass.
    Leaves ticket in awaiting_validation and returns False otherwise.
    """
    size = t.get("size", "")
    if size not in ("S", "M"):
        _log({"action": "auto_validate_skip", "id": t["id"], "reason": f"size={size}"})
        return False

    desc = (t.get("description") or "").lower()
    for pattern in _HIGH_INERTIA_PATTERNS:
        if pattern.lower() in desc:
            _log(
                {
                    "action": "auto_validate_skip",
                    "id": t["id"],
                    "reason": f"high_inertia:{pattern}",
                }
            )
            return False

    result = (t.get("result") or "").lower()
    if "fail" in result or "scope_guard" in result or "skipped" in result:
        _log(
            {
                "action": "auto_validate_skip",
                "id": t["id"],
                "reason": f"result_flags:{result[:60]}",
            }
        )
        return False

    if t.get("worker") != "igor":
        _log(
            {"action": "auto_validate_skip", "id": t["id"], "reason": "worker_not_igor"}
        )
        return False

    # All criteria pass — close immediately
    t["status"] = "closed"
    t["title"] = _with_status_prefix("closed", t["title"])
    t["auto_validated"] = True
    decision_id = t.get("decision_id")
    _decision_rollup(tasks, decision_id)
    _ungate_dependents(tasks, t["id"])
    _save(tasks)
    _log({"action": "auto_validated", "id": t["id"], "title": t["title"]})
    _prepend_closed_ticket(t["id"], t["title"])
    _append_to_todays_slate(t)
    print(f"Auto-validated {t['id']}: {t['title']}")
    return True


def cmd_done(args):
    """Igor's submit path — marks awaiting_validation. CC validates via cmd_close.

    Usage: done <id> <result-message> [--commit <hash>|--no-commit <reason>]

    --commit <hash>      : git commit hash for the code change (7-40 hex chars)
    --no-commit <reason> : explicit declaration that no code was changed

    At least one is required. When neither is supplied, a WARNING is emitted and
    validation is forced to manual (no auto-validate). Future: hard error once
    Igor's pe_chain passes --commit on every close.
    """
    import re as _re

    if len(args) < 2:
        print(
            "Usage: done <id> <result-message> [--commit <hash>|--no-commit <reason>]"
        )
        sys.exit(1)

    ticket_id = args[0]
    result_msg = args[1]
    remaining = args[2:]

    commit_hash = None
    no_commit_reason = None

    i = 0
    while i < len(remaining):
        if remaining[i] == "--commit" and i + 1 < len(remaining):
            commit_hash = remaining[i + 1]
            i += 2
        elif remaining[i] == "--no-commit" and i + 1 < len(remaining):
            no_commit_reason = remaining[i + 1]
            i += 2
        else:
            i += 1

    # Backwards-compat: detect a 7-40 hex commit hash embedded in the result string
    if not commit_hash and not no_commit_reason:
        m = _re.search(r"\b([0-9a-f]{7,40})\b", result_msg)
        if m:
            commit_hash = m.group(1)

    missing_evidence = not commit_hash and not no_commit_reason
    if missing_evidence:
        print(
            f"WARN: done {ticket_id} — no --commit or --no-commit supplied. "
            "Skipping auto-validate; CC must validate manually. "
            "(Future: this will become a hard error once pe_chain passes --commit.)"
        )

    tasks = _load()
    t = _find(tasks, ticket_id)
    if not t:
        print(f"Task {ticket_id} not found.")
        sys.exit(1)
    t["status"] = "awaiting_validation"
    t["title"] = _with_status_prefix("awaiting_validation", t["title"])
    t["result"] = result_msg
    t["completed_at"] = _now()
    if commit_hash:
        t["commit_hash"] = commit_hash
    if no_commit_reason:
        t["no_commit_reason"] = no_commit_reason
    _save(tasks)
    _log(
        {
            "action": "awaiting_validation",
            "id": ticket_id,
            "title": t["title"],
            "result": result_msg,
            "commit_hash": commit_hash,
            "no_commit_reason": no_commit_reason,
        }
    )
    _close_igor_goal(ticket_id)
    # When commit evidence is missing, force manual review — skip auto-validate
    if not missing_evidence and _try_auto_validate(tasks, t):
        return
    _append_to_todays_slate(t)
    print(f"Awaiting validation {ticket_id}: {t['title']}")


def cmd_close(args):
    """CC's validated-close path — marks closed, runs rollup and ungate."""
    if len(args) < 2:
        print("Usage: close <id> <result-message>")
        sys.exit(1)
    tasks = _load()
    t = _find(tasks, args[0])
    if not t:
        print(f"Task {args[0]} not found.")
        sys.exit(1)
    t["status"] = "closed"
    t["title"] = _with_status_prefix("closed", t["title"])
    t["result"] = args[1]
    t["completed_at"] = _now()
    cost_usd = _compute_cost_usd(args[0])
    if cost_usd is not None:
        t["cost_usd"] = round(cost_usd, 4)
    decision_id = t.get("decision_id")
    _decision_rollup(tasks, decision_id)
    _ungate_dependents(tasks, t["id"])
    _save(tasks)
    _log({"action": "close", "id": args[0], "title": t["title"], "result": args[1], "cost_usd": t.get("cost_usd")})
    _prepend_closed_ticket(args[0], t["title"])
    _close_igor_goal(args[0])
    _classifier_clear_in_flight(args[0])
    _annotator_delta_update(args[0])
    _append_to_todays_slate(t)
    cost_str = f"  cost=${t['cost_usd']:.4f}" if t.get("cost_usd") is not None else ""
    print(f"Closed {args[0]}: {t['title']}{cost_str}")


def cmd_block(args):
    if len(args) < 2:
        print("Usage: block <id> <reason>")
        sys.exit(1)
    tasks = _load()
    t = _find(tasks, args[0])
    if not t:
        print(f"Task {args[0]} not found.")
        sys.exit(1)
    t["status"] = "hold"
    t["title"] = _with_status_prefix("hold", t["title"])
    t["result"] = args[1]
    t["blocked_at"] = _now()
    _save(tasks)
    _log({"action": "hold", "id": args[0], "title": t["title"], "reason": args[1]})
    _close_igor_goal(args[0])
    print(f"Hold {args[0]}: {args[1]}")


def cmd_propose(args):
    """D331: Igor proposes a design change for approval. Sets status=approval."""
    if len(args) < 2:
        print("Usage: propose <id> <proposal text>")
        sys.exit(1)
    tasks = _load()
    t = _find(tasks, args[0])
    if not t:
        print(f"Task {args[0]} not found.")
        sys.exit(1)
    proposal = " ".join(args[1:])
    t["status"] = "approval"
    t["title"] = _with_status_prefix("approval", t["title"])
    t["proposal"] = proposal
    t["proposed_at"] = _now()
    _save(tasks)
    _log(
        {
            "action": "propose",
            "id": args[0],
            "title": t["title"],
            "proposal": proposal[:200],
        }
    )
    print(f"Proposed {args[0]}: {proposal[:120]}")
    print(f"Status: approval — CC will review on next context-load")


def cmd_approve(args):
    """D331: Approve a pending proposal. Resets ticket to sprint with approved plan."""
    if not args:
        print("Usage: approve <id> [approval notes]")
        sys.exit(1)
    tasks = _load()
    t = _find(tasks, args[0])
    if not t:
        print(f"Task {args[0]} not found.")
        sys.exit(1)
    if t["status"] not in ("approval", "awaiting_approval"):
        print(f"Task {args[0]} is {t['status']}, not approval.")
        sys.exit(1)
    notes = " ".join(args[1:]) if len(args) > 1 else ""
    t["status"] = "sprint"
    t["title"] = _with_status_prefix("sprint", t["title"])
    t["approved_plan"] = t.get("proposal", "")
    t["approval_notes"] = notes
    t["approved_at"] = _now()
    t["blocked_at"] = None  # Clear any prior block
    _save(tasks)
    _log(
        {"action": "approve", "id": args[0], "title": t["title"], "notes": notes[:200]}
    )
    print(f"Approved {args[0]}: {t['title']}")
    if notes:
        print(f"Notes: {notes}")

    # D333: notify Igor so he re-adopts without waiting 30min PROC_QUEUE_DRAIN
    try:
        import urllib.request

        cc_send_url = os.environ.get("CC_SEND_URL", "http://localhost:8080/api/cc_send")
        msg = (
            f"[APPROVED] {args[0]} approved by CC. "
            f"adopt top ticket. {f'Notes: {notes[:100]}' if notes else ''}"
        )
        req = urllib.request.Request(
            cc_send_url,
            data=json.dumps({"content": msg}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
        print("Notified Igor via cc_send")
    except Exception as e:
        print(f"Igor notification failed (non-fatal): {e}")

    print("Status: pending — Igor notified, will adopt on next turn")


def cmd_log(args):
    if not args:
        print("Usage: log <message>")
        sys.exit(1)
    msg = " ".join(args)
    _log({"action": "note", "message": msg})
    print(f"Logged: {msg}")


# ── Worker auto-default (D-worker-mode-routing-2026-04-21) ────────────────────
#
# HIGH-inertia or XL-sized tickets route to CC (reviewable konsole-spawn).
# Everything else routes to Igor (cheap in-process via engram chain / Qwen).
# Explicit `worker` in input JSON always wins.
#
# Keep these heuristics synced with lab/unseenuniversity/rules/coding.md
# ("Inertia levels") and decision D-worker-mode-routing-2026-04-21.

_HIGH_INERTIA_TAGS = {"HIGH", "high-inertia", "HIGH-inertia", "high_inertia"}
_HIGH_INERTIA_PATHS = (
    "brainstem/",
    "memory/models.py",
    "cognition/reasoners/base.py",
)


def _infer_worker(t: dict) -> str:
    """Route ticket to 'claude' (reviewable) for HIGH-inertia work, else unassigned.

    Rule:
      HIGH-inertia tag OR size=XL OR description touches HIGH-inertia paths
        → 'claude' (CC reviews; konsole-spawned session).
      Everything else → None (unassigned; awaits explicit assignment by Akien).

    Callers should only invoke this when the ticket has no explicit 'worker'.
    """
    tags = t.get("tags") or []
    if any(tag in _HIGH_INERTIA_TAGS for tag in tags):
        return "claude"

    size = (t.get("size") or "").upper()
    if size == "XL":
        return "claude"

    # Scan title + description for HIGH-inertia code paths
    blob_parts = [t.get("title") or "", t.get("description") or "", t.get("body") or ""]
    for f in t.get("required_files") or []:
        blob_parts.append(f)
    blob = " ".join(blob_parts)
    for path in _HIGH_INERTIA_PATHS:
        if path in blob:
            return "claude"

    return None  # unassigned; awaits Akien assignment


def _infer_role(t: dict) -> str:
    """Return the role for a ticket, inferring from `worker` when `role` is absent.

    Role encodes the minimum capability level needed to execute the ticket.
    Returns a string from VALID_ROLES; defaults to 'apprentice'.
    """
    role = (t.get("role") or "").strip().lower()
    if role in VALID_ROLES:
        return role
    worker = (t.get("worker") or "").lower()
    return _WORKER_TO_ROLE.get(worker, "apprentice")


def _scraps_validate(ticket: dict) -> bool:
    """Pre-flight: call ScrapsDevice.validate_ticket(); degrade gracefully if offline.

    Returns True (proceed) or False (caller should abort transition).
    On pass, stamps ticket['scraps_validated'] = validated_at in-place.
    On offline, prints a warning and returns True (never hard-block on device offline).
    On invalid, prints the issue list and returns False.
    """
    try:
        from devices.scraps.scraps_device import ScrapsDevice

        # silent=True: cc_queue already reports issues to stdout; channel post is redundant
        # and would write to the real Postgres channel during test runs.
        result = ScrapsDevice().validate_ticket(ticket, silent=True)
    except Exception as exc:
        print(f"Scraps offline — validation skipped ({exc})")
        return True

    if result.get("valid"):
        ticket["scraps_validated"] = result["validated_at"]
        return True

    issues = result.get("issues") or ["unknown issue"]
    print("Scraps validation failed:")
    for issue in issues:
        print(f"  - {issue}")
    return False


_REQUIRED_DESCRIPTION_SECTIONS = [
    "**Affected files:**",
    "**Scope boundary:**",
    "**Completion criteria:**",
]


def _check_description_contracts(ticket_id: str, description: str) -> None:
    """Warn when required description sections are missing. Non-blocking."""
    if not description:
        return
    missing = [s for s in _REQUIRED_DESCRIPTION_SECTIONS if s not in description]
    if missing:
        print(
            f"  WARNING ({ticket_id}): description missing required section(s): "
            + ", ".join(missing)
        )


def _check_intention_field(ticket_id: str, intention: str | None) -> None:
    """Warn when intention: field is missing or doesn't start with 'I intend'. Non-blocking."""
    if not intention or not str(intention).strip():
        print(
            f"  WARNING ({ticket_id}): missing intention: field — add 'I intend that...' "
            "statement (IBD root artifact; D-intention-based-development-2026-06-04)"
        )


def _decorate_with_intent(ticket: dict) -> None:
    """Intent extractor decoration hook: predict or validate ticket intention.

    Graceful degradation: if intent extractor device is unavailable, proceed normally
    with a warning. Never raises.
    """
    try:
        from devices.intent.tools import intent_predict, intent_validate
    except ImportError:
        # Device or tools module not available
        _log({"action": "intent_decorate_skip", "id": ticket["id"], "reason": "import_failed"})
        return

    ticket_id = ticket.get("id")
    if not ticket_id:
        return

    try:
        # If intention field is blank, predict it from description
        if not ticket.get("intention") or not str(ticket.get("intention")).strip():
            description = ticket.get("description", "")[:500]
            if description:
                result = intent_predict(context=description, domain="coding")
                if result:
                    predicted = result.get("intent", "")
                    prediction_id = result.get("prediction_id")
                    if predicted:
                        ticket["inferred_intention"] = predicted
                        ticket["inferred_intention_id"] = prediction_id
                        _log({
                            "action": "intent_decorate_predict",
                            "id": ticket_id,
                            "predicted": predicted,
                            "confidence": result.get("confidence", 0.0),
                        })
        # If intention field exists, validate it as ground truth
        elif ticket.get("intention"):
            intention = str(ticket.get("intention")).strip()
            result = intent_predict(context=ticket.get("description", "")[:500], domain="coding")
            if result:
                prediction_id = result.get("prediction_id")
                intent_validate(actual_outcome=intention, prediction_id=prediction_id)
                _log({
                    "action": "intent_decorate_validate",
                    "id": ticket_id,
                    "intention": intention,
                })
    except Exception as exc:
        # Fail open: log warning but continue
        _log({
            "action": "intent_decorate_error",
            "id": ticket_id,
            "error": str(exc)[:200],
        })


def cmd_add(args):
    """Add tasks from a JSON file (array of task objects) or inline JSON string."""
    if not args:
        print("Usage: add <json-file-or-inline-json>")
        sys.exit(1)
    src = args[0]
    if os.path.exists(src):
        with open(src) as f:
            new_tasks = json.load(f)
    else:
        new_tasks = json.loads(src)
    if isinstance(new_tasks, dict):
        new_tasks = [new_tasks]
    tasks = _load()
    existing_ids = {t["id"] for t in tasks}
    added = 0
    for nt in new_tasks:
        # Guard: reject IDs that were corrupted by CC's privacy filter at write-time.
        # The filter replaces values it classifies as credentials with the literal
        # string '[REDACTED-CREDENTIAL]', which then gets stored in the DB as the ID.
        if not nt.get("id") or str(nt["id"]).startswith("[REDACTED"):
            print(
                f"  blocked: ticket has corrupted id={nt.get('id')!r} "
                "(CC privacy filter replaced the real ID at write-time — "
                "re-file with a plain T-<kebab-slug> id)"
            )
            continue
        if nt["id"] in existing_ids:
            print(f"  skip (exists): {nt['id']}")
            continue
        nt.setdefault("status", "triage")
        nt.setdefault("created_at", _now())
        # D-worker-mode-routing-2026-04-21: auto-default by metadata if unset
        if "worker" not in nt or nt.get("worker") in (None, ""):
            nt["worker"] = _infer_worker(nt)
        nt.setdefault("created_by", None)
        nt.setdefault("result", None)
        nt.setdefault("dispatched_at", None)
        nt.setdefault("completed_at", None)
        nt.setdefault("required_files", [])
        nt.setdefault("related_to", None)
        nt.setdefault("github_issue", None)
        nt.setdefault("decision_id", None)
        nt.setdefault("gate", None)
        nt.setdefault("intention", None)
        nt.setdefault("target_difficulty", 1)
        # Set role: explicit value wins; otherwise infer from worker.
        if not nt.get("role"):
            nt["role"] = _infer_role(nt)
        elif nt["role"] not in VALID_ROLES:
            print(
                f"  blocked: {nt['id']} — role must be one of {sorted(VALID_ROLES)}, got {nt['role']!r}"
            )
            continue
        try:
            diff = int(nt["target_difficulty"])
        except (ValueError, TypeError):
            diff = -1
        if diff not in DIFFICULTY_TIERS:
            print(
                f"  blocked: {nt['id']} — target_difficulty must be 1-5 (Apprentice→Teacher), got {nt['target_difficulty']!r}"
            )
            continue
        nt["target_difficulty"] = diff
        # Scraps pre-flight runs before status prefix is applied so the
        # original title is visible to the generic-title check.
        if not _scraps_validate(nt):
            print(f"  blocked: {nt['id']} — fix issues above to add.")
            continue
        _check_description_contracts(nt["id"], nt.get("description", ""))
        _check_intention_field(nt["id"], nt.get("intention"))
        # Intent extractor decoration: predict intention or validate if already present
        _decorate_with_intent(nt)
        # Embed status prefix in title for one-grep searchability
        nt["title"] = _with_status_prefix(nt["status"], nt["title"])
        tasks.append(nt)
        _log({"action": "add", "id": nt["id"], "title": nt["title"]})
        print(f"  added: {nt['id']} — {nt['title']}")
        added += 1
    _save(tasks)
    print(f"Added {added} task(s).")


def _igor_post(content: str, tag: str) -> bool:
    """POST a message to UC's /api/cc_send as author 'claude-code'.

    tag is a short label used for failure logging only.
    """
    data = json.dumps({"content": content}).encode()
    req = urllib.request.Request(
        IGOR_FLUSH_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5, context=_ssl_ctx()):
            return True
    except Exception as e:
        _log({"action": "flush_failed", "error": str(e), "tag": tag})
        print(f"  [Igor flush failed — UC not running? {e}]")
        return False


def cmd_flush_decision(args):
    """Post a design-decision flush to the channel (author: claude-code)."""
    if len(args) < 2:
        print("Usage: flush_decision <id> <summary>")
        sys.exit(1)
    decision_id = args[0]
    summary = " ".join(args[1:])
    content = f"[FLUSH decision {decision_id}] {summary}"
    if _igor_post(content, tag=decision_id):
        _log({"action": "flush_decision", "id": decision_id, "summary": summary})
        print(f"Flushed {decision_id} to Igor: {summary[:80]}")
    else:
        print(f"  (decision logged locally only)")


def cmd_flush_session(args):
    """Post a session-summary flush to the channel (author: claude-code)."""
    if len(args) < 2:
        print("Usage: flush_session <session_id> <summary>")
        sys.exit(1)
    session_id = args[0]
    summary = " ".join(args[1:])
    content = f"[FLUSH session {session_id}] {summary}"
    if _igor_post(content, tag=f"session_{session_id}"):
        _log({"action": "flush_session", "session": session_id})
        print(f"Flushed session {session_id} to Igor")
    else:
        print(f"  (session logged locally only)")


WORKER_PIDS_PATH = os.path.expanduser(
    "~/.unseen_university/cc_channel/worker_pids.json"
)
DAEMON_PID_FILE = os.path.expanduser(
    "~/.unseen_university/cc_channel/worker_daemon.pid"
)
DAEMON_SCRIPT = os.path.expanduser("~/.unseen_university/bin/worker_daemon.sh")


def _load_worker_pids():
    if not os.path.exists(WORKER_PIDS_PATH):
        return {}
    with open(WORKER_PIDS_PATH) as f:
        return json.load(f)


def _save_worker_pids(pids):
    os.makedirs(os.path.dirname(WORKER_PIDS_PATH), exist_ok=True)
    with open(WORKER_PIDS_PATH, "w") as f:
        json.dump(pids, f, indent=2)


def _daemon_alive():
    """Return daemon PID if running, else None."""
    if not os.path.exists(DAEMON_PID_FILE):
        return None
    try:
        pid = int(open(DAEMON_PID_FILE).read().strip())
        os.kill(pid, 0)
        return pid
    except (ValueError, ProcessLookupError, PermissionError):
        return None


def cmd_notify_igor(args):
    """Send a message to Igor via the cc_send bridge (POST /api/cc_send)."""
    if not args:
        print("Usage: notify-igor <message>")
        sys.exit(1)
    msg = " ".join(args)
    data = json.dumps({"content": msg}).encode()
    req = urllib.request.Request(
        "https://localhost:8080/api/cc_send",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5, context=_ssl_ctx()):
            print(f"sent to Igor: {msg}")
    except Exception as e:
        _log({"action": "notify_igor_failed", "error": str(e), "msg": msg})
        print(f"  [notify-igor failed — Igor not running? {e}]")


def cmd_worker_launch(args):
    """Ensure the worker daemon is running. Spawns a konsole if not already alive.

    The daemon (worker_daemon.sh) polls the queue and runs /sprint for each
    pending ticket automatically — no xdotool injection needed.
    Ticket-id argument is accepted but ignored (daemon finds next pending itself).
    """
    import subprocess

    pid = _daemon_alive()
    if pid:
        print(
            f"Worker daemon already running (PID {pid}) — will pick up next pending ticket automatically."
        )
        return

    proc = subprocess.Popen(
        [
            "konsole",
            "--separate",
            "-e",
            "bash",
            "-c",
            f"bash {DAEMON_SCRIPT}; exec bash",
        ],
        start_new_session=True,
    )
    pids = _load_worker_pids()
    pids["daemon"] = {
        "konsole_pid": proc.pid,
        "launched_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_worker_pids(pids)
    print(f"Launched worker daemon — konsole PID {proc.pid}")


def _trip_gate(ticket_id: str, reason: str) -> None:
    """Write GATE_FILE to circuit-break the daemon queue."""
    os.makedirs(os.path.dirname(GATE_FILE), exist_ok=True)
    gate_data = {
        "tripped": True,
        "reason": reason,
        "ticket_id": ticket_id,
        "tripped_at": _now(),
    }
    with open(GATE_FILE, "w") as f:
        json.dump(gate_data, f, indent=2)
    _log({"action": "gate_tripped", "ticket_id": ticket_id, "reason": reason})
    print(f"GATE TRIPPED: {ticket_id} — {reason}")


def _priority_key(t):
    p = t.get("priority", 99)
    try:
        v = float(str(p).lstrip("pP"))
        # 0-1 floats are importance scores (higher=better) → negate for min-sort
        # integers ≥2 are P-numbers (lower=better) → use directly
        return -v if v <= 1.0 else v
    except (ValueError, TypeError):
        return 99.0


def next_ticket_id_for_worker(
    worker: str, max_difficulty: "int | None" = None
) -> "str | None":
    """Return the highest-priority sprint ticket ID for a worker, or None.

    worker must be supplied — 'igor', 'claude', or another named worker.
    max_difficulty=N    → only tickets where target_difficulty <= N (unset → treated as 1)

    Respects GATE_FILE circuit breaker — returns None when gate is tripped.
    Does NOT claim the ticket — cmd_next performs the atomic claim.
    """
    if os.path.exists(GATE_FILE):
        try:
            gate_data = json.loads(open(GATE_FILE).read())
            if gate_data.get("tripped"):
                return None
        except Exception:
            pass  # corrupt gate file → treat as not tripped

    tasks = _load()
    candidates = [
        t
        for t in tasks
        if t.get("status") == "sprint"
        and not t.get("gate")
        and t.get("worker") == worker
    ]
    if max_difficulty is not None:
        candidates = [
            t for t in candidates if t.get("target_difficulty", 1) <= max_difficulty
        ]
    if not candidates:
        return None
    best = min(candidates, key=_priority_key)
    return best["id"]


def cmd_next(args):
    """Claim and return the highest-priority sprint ticket for a worker.

    --worker <name>:       required — worker requesting a ticket (e.g. igor, claude).
    --max-difficulty=N:    only return tickets where target_difficulty <= N.

    Errors (exit 1) when --worker is omitted — direct claiming without a worker name
    is no longer allowed.  Atomically marks the ticket in_progress before printing
    its ID so no other worker can race for the same ticket.
    Respects GATE_FILE circuit breaker — prints nothing when gate is tripped.
    Output: one ticket ID line, or nothing if gate tripped / queue empty.
    """
    if "--worker" not in args:
        print(
            "ERROR: cc_queue.py next requires --worker <name>.\n"
            "Usage: cc_queue.py next --worker <name> [--max-difficulty=N]",
            file=sys.stderr,
        )
        sys.exit(1)

    worker_filter = None
    max_difficulty = None
    remaining = list(args)

    i = remaining.index("--worker")
    if i + 1 >= len(remaining):
        print("ERROR: --worker requires a value.", file=sys.stderr)
        sys.exit(1)
    worker_filter = remaining[i + 1]
    del remaining[i : i + 2]

    for arg in remaining:
        if arg.startswith("--max-difficulty="):
            try:
                max_difficulty = int(arg.split("=", 1)[1])
            except ValueError:
                print(f"Invalid --max-difficulty value: {arg}", file=sys.stderr)
                sys.exit(1)

    ticket_id = next_ticket_id_for_worker(worker_filter, max_difficulty)
    if not ticket_id:
        return

    # Atomically mark in_progress so no other worker can race for this ticket.
    import psycopg2

    conn = _db_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT metadata FROM clan.memories WHERE id = %s FOR UPDATE",
            (ticket_id,),
        )
        row = cur.fetchone()
        if not row or not row[0]:
            conn.rollback()
            return
        t = dict(row[0])
        t.pop("kind", None)
        if t.get("status") != "sprint":
            conn.rollback()
            return  # another worker claimed it between our read and lock
        now = _now()
        t["status"] = "in_progress"
        t["title"] = _with_status_prefix("in_progress", t["title"])
        t["dispatched_at"] = now
        metadata = dict(t)
        metadata["kind"] = "ticket"
        cur.execute(
            """UPDATE clan.memories SET
                metadata = %s::jsonb,
                narrative = %s,
                updated_at = %s
            WHERE id = %s""",
            (json.dumps(metadata), _narrative_for(t), now, ticket_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    _log({"action": "dispatch_via_next", "id": ticket_id, "worker": worker_filter})
    _classifier_stamp_in_flight(ticket_id, t.get("required_files", []))
    print(ticket_id)


def cmd_reset(args):
    """Reset a single ticket back to sprint (e.g., after a timeout).

    --timeout: daemon timeout reset — increments timeout_count in ticket
               metadata and trips GATE_FILE after 3 consecutive timeouts.
    """
    timeout_mode = "--timeout" in args
    clean_args = [a for a in args if a != "--timeout"]
    if not clean_args:
        print("Usage: reset [--timeout] <id>")
        sys.exit(1)
    tasks = _load()
    t = _find(tasks, clean_args[0])
    if not t:
        print(f"Task {clean_args[0]} not found.")
        sys.exit(1)
    prev = t["status"]
    if prev in _TERMINAL_STATUSES:
        print(
            f"Skipping reset of {clean_args[0]}: already terminal ({prev}) — will not reopen."
        )
        return
    t["status"] = "sprint"
    t["dispatched_at"] = None
    t["blocked_at"] = None
    if timeout_mode:
        count = (t.get("timeout_count") or 0) + 1
        t["timeout_count"] = count
        _log({"action": "timeout_reset", "id": clean_args[0], "timeout_count": count})
        if count >= 3:
            _trip_gate(clean_args[0], f"{count} consecutive timeouts")
    _save(tasks)
    _log({"action": "reset", "id": clean_args[0], "prev_status": prev})
    print(f"Reset {clean_args[0]}: {prev} → sprint (blocked_at cleared)")


def cmd_reset_stale(args):
    """Reset all in_progress tickets back to sprint (used at daemon startup to clean orphans)."""
    tasks = _load()
    reset_count = 0
    for t in tasks:
        if t["status"] == "in_progress":
            prev = t["status"]
            t["status"] = "sprint"
            t["dispatched_at"] = None
            _log({"action": "reset_stale", "id": t["id"], "prev_status": prev})
            print(f"  reset stale: {t['id']}")
            reset_count += 1
    if reset_count:
        _save(tasks)
    print(f"Reset {reset_count} stale in_progress ticket(s).")


_VALID_STATUSES = set(STATUS_ORDER.keys())


def cmd_setstatus(args):
    """Set any status directly: setstatus <id> <status>"""
    if len(args) < 2:
        print("Usage: setstatus <ticket-id> <status>")
        print(f"Valid: {', '.join(sorted(_VALID_STATUSES))}")
        sys.exit(1)
    tid, new_status = args[0], args[1]
    if new_status not in _VALID_STATUSES:
        print(
            f"Unknown status {new_status!r}. Valid: {', '.join(sorted(_VALID_STATUSES))}"
        )
        sys.exit(1)
    tasks = _load()
    t = _find(tasks, tid)
    if not t:
        print(f"Task {tid} not found.")
        sys.exit(1)
    old_status = t["status"]
    t["status"] = new_status
    t["title"] = _with_status_prefix(new_status, t["title"])
    _save(tasks)
    _log({"action": "setstatus", "id": tid, "old": old_status, "new": new_status})
    print(f"{tid}: {old_status} → {new_status}")


# Title prefix → canonical status mapping for migrate-statuses
_PREFIX_STATUS = {
    "DESIGNED:": "sprint",
    "NEEDS DESIGN:": "design",
    "NEW:": "triage",
    "CLOSED:": "hold",
}

# Per-ticket status overrides (id → status) for migrate-statuses
_ID_STATUS_OVERRIDE = {
    "T-uc-cert-domain-migration": "akien",
}


def cmd_migrate_statuses(args):
    """One-time migration: strip title prefixes, map old statuses to new canonical values."""
    tasks = _load()
    changed = 0
    for t in tasks:
        if t.get("status") in _TERMINAL_STATUSES:
            continue
        old_title = t.get("title", "")
        old_status = t.get("status", "")
        new_title = old_title
        new_status = old_status

        # Strip known prefixes and derive status from them
        for prefix, derived_status in _PREFIX_STATUS.items():
            if old_title.startswith(prefix):
                new_title = old_title[len(prefix) :].strip()
                # Only apply prefix-derived status if status is still "pending"
                if old_status == "pending":
                    new_status = derived_status
                break

        # Map legacy statuses to new canonical names
        legacy_map = {
            "blocked": "hold",
            "awaiting_approval": "approval",
            "needs_review": "triage",
        }
        if new_status in legacy_map:
            new_status = legacy_map[new_status]

        # Per-ticket overrides
        if t["id"] in _ID_STATUS_OVERRIDE:
            new_status = _ID_STATUS_OVERRIDE[t["id"]]

        if new_title != old_title or new_status != old_status:
            print(f"  {t['id']}: [{old_status}] {old_title!r}")
            print(f"    → [{new_status}] {new_title!r}")
            t["title"] = new_title
            t["status"] = new_status
            changed += 1

    if changed:
        _save(tasks)
        print(f"\nMigrated {changed} ticket(s).")
    else:
        print("Nothing to migrate.")


def cmd_append_note(args):
    """Append a note block to a ticket's description: append-note <id> <text>

    Appends the text on a new line preceded by a blank line. Used by DickSimnel
    and other devices to attach escalation summaries without overwriting description.
    """
    if len(args) < 2:
        print("Usage: append-note <id> <text>")
        sys.exit(1)
    tid = args[0]
    note = " ".join(args[1:])
    tasks = _load()
    t = _find(tasks, tid)
    if not t:
        print(f"Task {tid} not found.")
        sys.exit(1)
    existing = t.get("description") or ""
    t["description"] = existing.rstrip("\n") + "\n\n" + note
    _save(tasks)
    _log({"action": "append_note", "id": tid, "note_len": len(note)})
    print(f"Note appended to {tid}")


def cmd_stamp_verdict(args):
    """Stamp a post-sprint grader verdict onto a ticket: stamp-verdict <id> <pass|fail|partial> [<reasoning>]

    Adds a 'verdict' field to the ticket metadata so cc_queue.py show surfaces it.
    Advisory only — never affects ticket status or closes the ticket.
    """
    if len(args) < 2:
        print("Usage: stamp-verdict <id> <pass|fail|partial> [<reasoning>]")
        sys.exit(1)
    tid = args[0]
    verdict_value = args[1]
    if verdict_value not in ("pass", "fail", "partial"):
        print(f"Invalid verdict {verdict_value!r} — must be pass, fail, or partial")
        sys.exit(1)
    reasoning = " ".join(args[2:]) if len(args) > 2 else ""
    tasks = _load()
    t = _find(tasks, tid)
    if not t:
        print(f"Task {tid} not found.")
        sys.exit(1)
    t["verdict"] = verdict_value
    if reasoning:
        t["verdict_reasoning"] = reasoning[:500]
    _save(tasks)
    _log({"action": "stamp_verdict", "id": tid, "verdict": verdict_value})
    reasoning_suffix = f" — {reasoning[:80]}" if reasoning else ""
    print(f"Verdict stamped on {tid}: {verdict_value}{reasoning_suffix}")


COMMANDS = {
    "list": cmd_list,
    "show": cmd_show,
    "done": cmd_done,
    "close": cmd_close,
    "block": cmd_block,
    "propose": cmd_propose,
    "approve": cmd_approve,
    "log": cmd_log,
    "add": cmd_add,
    "append-note": cmd_append_note,
    "stamp-verdict": cmd_stamp_verdict,
    "flush_decision": cmd_flush_decision,
    "flush_session": cmd_flush_session,
    "worker-launch": cmd_worker_launch,
    "notify-igor": cmd_notify_igor,
    "next": cmd_next,
    "dispatch": cmd_dispatch,
    "reset": cmd_reset,
    "reset-stale": cmd_reset_stale,
    "setstatus": cmd_setstatus,
    "migrate-statuses": cmd_migrate_statuses,
}


def cmd_set_epic(args):
    """Set the epic tag on one or more tickets: set-epic <epic> <id> [<id> ...]"""
    if len(args) < 2:
        print("Usage: set-epic <epic> <ticket-id> [<ticket-id> ...]")
        sys.exit(1)
    epic, ids = args[0], args[1:]
    tasks = _load()
    idx = {t["id"]: t for t in tasks}
    for tid in ids:
        if tid not in idx:
            print(f"  not found: {tid}")
            continue
        idx[tid]["epic"] = epic
        print(f"  {tid} → #{epic}")
    _save(tasks)


COMMANDS["set-epic"] = cmd_set_epic


def cmd_set_worker(args):
    """Assign worker to one or more tickets: set-worker <worker> <id> [<id> ...]"""
    if len(args) < 2:
        print("Usage: set-worker <worker> <ticket-id> [<ticket-id> ...]")
        sys.exit(1)
    worker, ids = args[0], args[1:]
    known = ("igor", "claude", "dicksimnel", "akien")
    if worker and worker not in known:
        print(f"Unknown worker '{worker}' — use one of: {', '.join(known)} or '' to clear")
        sys.exit(1)
    tasks = _load()
    idx = {t["id"]: t for t in tasks}
    for tid in ids:
        if tid not in idx:
            print(f"  not found: {tid}")
            continue
        idx[tid]["worker"] = worker or None
        label = worker if worker else "(cleared)"
        print(f"  {tid} → worker={label}")
    _save(tasks)


COMMANDS["set-worker"] = cmd_set_worker


def cmd_set_role(args):
    """Set the role field on one or more tickets: set-role <role> <id> [<id> ...]
    Role ladder: guru (Akien), master (CC.0), builder (DickSimnel), creator (alias builder)."""
    if len(args) < 2:
        print("Usage: set-role <role> <ticket-id> [<ticket-id> ...]")
        sys.exit(1)
    role, ids = args[0], args[1:]
    known = ("guru", "master", "builder", "creator")
    if role not in known:
        print(f"Unknown role '{role}' — use one of: {', '.join(known)}")
        sys.exit(1)
    tasks = _load()
    idx = {t["id"]: t for t in tasks}
    for tid in ids:
        if tid not in idx:
            print(f"  not found: {tid}")
            continue
        idx[tid]["role"] = role
        print(f"  {tid} → role={role}")
    _save(tasks)


COMMANDS["set-role"] = cmd_set_role


def cmd_needs_review(args):
    """Mark a ticket needs_review — Igor self-coding review gate."""
    if not args:
        print("Usage: needs-review <id>")
        sys.exit(1)
    tasks = _load()
    t = _find(tasks, args[0])
    if not t:
        print(f"Task {args[0]} not found.")
        sys.exit(1)
    t["status"] = "needs_review"
    t["needs_review_at"] = _now()
    _save(tasks)
    _log({"action": "needs_review", "id": args[0], "title": t["title"]})
    print(f"Needs review: {args[0]}: {t['title']}")


COMMANDS["needs-review"] = cmd_needs_review


def cmd_gate(args):
    """Gate a ticket behind a precondition. Usage: gate <id> <reason>"""
    if len(args) < 2:
        print("Usage: gate <ticket-id> <reason-string>")
        sys.exit(1)
    tid = args[0]
    reason = " ".join(args[1:])
    tasks = _load()
    t = _find(tasks, tid)
    if not t:
        print(f"Task {tid} not found.")
        sys.exit(1)
    t["gate"] = reason
    _save(tasks)
    _log({"action": "gate", "id": tid, "reason": reason})
    print(f"Gated {tid}: {reason}")


COMMANDS["gate"] = cmd_gate


def cmd_ungate(args):
    """Clear a ticket's gate. Usage: ungate <id> [reason-cleared]"""
    if not args:
        print("Usage: ungate <ticket-id> [reason-cleared]")
        sys.exit(1)
    tid = args[0]
    reason = " ".join(args[1:]) if len(args) > 1 else None
    tasks = _load()
    t = _find(tasks, tid)
    if not t:
        print(f"Task {tid} not found.")
        sys.exit(1)
    prev = t.get("gate")
    t["gate"] = None
    _save(tasks)
    _log({"action": "ungate", "id": tid, "prev_gate": prev, "reason_cleared": reason})
    msg = f"Ungated {tid}"
    if prev:
        msg += f" (was: {prev})"
    if reason:
        msg += f" — {reason}"
    print(msg)


COMMANDS["ungate"] = cmd_ungate


def cmd_set_decision(args):
    """Attach a decision id to a ticket. Usage: set-decision <id> <decision-id>"""
    if len(args) < 2:
        print("Usage: set-decision <ticket-id> <decision-id>")
        sys.exit(1)
    tid, did = args[0], args[1]
    tasks = _load()
    t = _find(tasks, tid)
    if not t:
        print(f"Task {tid} not found.")
        sys.exit(1)
    t["decision_id"] = did
    _save(tasks)
    _log({"action": "set_decision", "id": tid, "decision_id": did})
    print(f"Set decision on {tid}: {did}")


COMMANDS["set-decision"] = cmd_set_decision


_IGOR_TAGS = {
    "cognition",
    "memory",
    "habits",
    "engrams",
    "narrativeengine",
    "twm",
}
_IGOR_REPO = "akienm/TheIgors"
_ADC_REPO = "akienm/unseen_university"


def _gh_repo_for(ticket: dict) -> str:
    """Return the GitHub repo slug for a ticket based on worker and tags.

    Routing rule: worker=igor OR tags intersect IGOR_TAGS → TheIgors.
    Everything else → unseen_university.
    """
    if ticket.get("worker") == "igor":
        return _IGOR_REPO
    tags_lower = {t.lower() for t in (ticket.get("tags") or [])}
    if tags_lower & _IGOR_TAGS:
        return _IGOR_REPO
    return _ADC_REPO


def cmd_set_github_issue(args):
    """Write a GitHub issue number back to a ticket: set-github-issue <id> <number> [--repo owner/repo]"""
    if len(args) < 2:
        print(
            "Usage: set-github-issue <ticket-id> <github-issue-number> [--repo owner/repo]"
        )
        sys.exit(1)
    tid, issue_num_str = args[0], args[1]
    repo_override = None
    remaining = args[2:]
    i = 0
    while i < len(remaining):
        if remaining[i] == "--repo" and i + 1 < len(remaining):
            repo_override = remaining[i + 1]
            i += 2
        else:
            i += 1
    try:
        issue_num = int(issue_num_str)
    except ValueError:
        print(f"Issue number must be an integer, got: {issue_num_str}")
        sys.exit(1)
    tasks = _load()
    t = _find(tasks, tid)
    if not t:
        print(f"Task {tid} not found.")
        sys.exit(1)
    repo = repo_override or _gh_repo_for(t)
    t["github_issue"] = issue_num
    _save(tasks)
    _log(
        {
            "action": "set_github_issue",
            "id": tid,
            "github_issue": issue_num,
            "repo": repo,
        }
    )
    print(f"Set {tid} github_issue → {issue_num} (repo: {repo})")


COMMANDS["set-github-issue"] = cmd_set_github_issue


def cmd_retitle(args):
    """Update a ticket's title: retitle <id> <new-title>"""
    if len(args) < 2:
        print("Usage: retitle <ticket-id> <new-title>")
        sys.exit(1)
    tid = args[0]
    new_title = args[1]
    tasks = _load()
    t = _find(tasks, tid)
    if not t:
        print(f"Task {tid} not found.")
        sys.exit(1)
    old_title = t["title"]
    t["title"] = new_title
    _save(tasks)
    _log(
        {"action": "retitle", "id": tid, "old_title": old_title, "new_title": new_title}
    )
    print(f"Retitled {tid}: {new_title!r}")


COMMANDS["retitle"] = cmd_retitle


def cmd_backfill_prefixes(args):
    """Add [status] prefix to all open tickets missing it. Safe to re-run."""
    tasks = _load()
    changed = 0
    for t in tasks:
        status = t.get("status", "triage")
        old_title = t.get("title", "")
        new_title = _with_status_prefix(status, old_title)
        if new_title != old_title:
            t["title"] = new_title
            changed += 1
            if "--verbose" in args:
                print(f"  {t['id']}: {old_title!r} → {new_title!r}")
    if changed:
        _save(tasks)
        print(f"Prefixed {changed} ticket(s).")
    else:
        print("All titles already have status prefixes.")


COMMANDS["backfill-prefixes"] = cmd_backfill_prefixes


def cmd_backfill_dates(args):
    """Fetch GitHub issue created_at for tickets missing created_at. Requires gh CLI."""
    import subprocess

    dry_run = "--dry-run" in args
    tasks = _load()
    need_dates = [t for t in tasks if not t.get("created_at") and t.get("github_issue")]
    print(f"{len(need_dates)} tickets need dates (have github_issue, no created_at)")
    if not need_dates:
        return
    changed = 0
    for t in need_dates:
        gh_num = t["github_issue"]
        try:
            result = subprocess.run(
                [
                    "gh",
                    "api",
                    f"repos/akienm/TheIgors/issues/{gh_num}",
                    "--jq",
                    ".created_at",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            date_str = result.stdout.strip()
            if date_str and not dry_run:
                t["created_at"] = date_str
                changed += 1
            print(f"  {t['id']} GH#{gh_num}: {date_str}{' (dry)' if dry_run else ''}")
        except Exception as e:
            print(f"  {t['id']} GH#{gh_num}: FAILED — {e}")
    if changed:
        _save(tasks)
        print(f"Backfilled dates for {changed} ticket(s).")


COMMANDS["backfill-dates"] = cmd_backfill_dates


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])
