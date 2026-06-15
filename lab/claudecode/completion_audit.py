#!/usr/bin/env python3
"""Completion audit — verify closed tickets were actually built.

Data layer for the day-close-audit Step 20 and ad-hoc invocation.
The reasoning (does code match criteria?) is done by the calling skill
(day-close-audit runs as Haiku and reads files to evaluate each ticket).

Usage:
  python3 completion_audit.py list [--days N] [--ticket T-xxx] [--json]
  python3 completion_audit.py log-result <ticket-id> <verdict> <reason>
  python3 completion_audit.py summary [--days N]

Verdicts: pass | fail | cannot-verify
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

IGOR_HOME = Path(os.environ.get("IGOR_HOME", Path.home() / ".unseen_university"))
TICKETS_ROOT_ID = "TICKETS_ROOT"
AUDIT_LOG = IGOR_HOME / "completion_audit.log"
_TERMINAL = {"closed", "done"}


def _db_conn():
    import psycopg2
    url = os.environ.get("UU_HOME_DB_URL") or os.environ.get(
        "IGOR_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"
    )
    return psycopg2.connect(url)


def extract_criteria(description: str) -> str | None:
    """Return the Completion criteria section, or None if absent."""
    m = re.search(
        r"\*\*Completion criteria:\*\*(.*?)(?=\n\*\*[A-Za-z]|\Z)",
        description or "",
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        crit = m.group(1).strip()
        return crit if crit else None
    return None


def get_closed_tickets(days: int = 1, ticket_id: str = None) -> list[dict]:
    """Fetch recently closed tickets from clan.memories."""
    conn = _db_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT metadata FROM clan.memories WHERE parent_id = %s",
            (TICKETS_ROOT_ID,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    results = []
    for (md,) in rows:
        if not md:
            continue
        t = dict(md)
        if t.get("status") not in _TERMINAL:
            continue
        if ticket_id and t.get("id") != ticket_id:
            continue
        if not ticket_id:
            raw_ts = t.get("completed_at") or t.get("deposited_at") or ""
            if raw_ts:
                try:
                    ts_str = raw_ts[:19].replace(" ", "T")
                    closed_at = datetime.fromisoformat(ts_str).replace(
                        tzinfo=timezone.utc
                    )
                    if closed_at < cutoff:
                        continue
                except ValueError:
                    pass
        results.append(
            {
                "id": t.get("id", ""),
                "title": (t.get("title") or "").replace("[sprint]", "").replace("[in_progress]", "").strip()[:80],
                "description": t.get("description") or "",
                "result": (t.get("result") or "")[:120],
                "completed_at": (t.get("completed_at") or t.get("deposited_at") or "")[:19],
                "criteria": extract_criteria(t.get("description") or ""),
                "size": t.get("size"),
                "status": t.get("status"),
            }
        )
    results.sort(key=lambda r: r["completed_at"], reverse=True)
    return results


def log_result(ticket_id: str, verdict: str, reason: str) -> None:
    """Append a completion-audit verdict to the audit log."""
    if verdict not in ("pass", "fail", "cannot-verify"):
        print(
            f"invalid verdict {verdict!r} — use: pass | fail | cannot-verify",
            file=sys.stderr,
        )
        sys.exit(1)
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {
        "ts": ts,
        "ticket_id": ticket_id,
        "verdict": verdict,
        "reason": reason,
    }
    with AUDIT_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    print(f"logged: {verdict} — {ticket_id}: {reason}")


def read_results(days: int = 7) -> list[dict]:
    """Read recent audit results from the log."""
    if not AUDIT_LOG.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    results = []
    for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            ts_str = entry.get("ts", "")[:19]
            ts = datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                results.append(entry)
        except (ValueError, KeyError, json.JSONDecodeError):
            continue
    return results


def cmd_list(args) -> None:
    tickets = get_closed_tickets(days=args.days, ticket_id=getattr(args, "ticket", None))
    if not tickets:
        if args.json:
            print("[]")
        else:
            print(f"No closed tickets found in the last {args.days} day(s).")
        return
    if args.json:
        # Omit full description to keep output compact
        out = [
            {
                "id": t["id"],
                "title": t["title"],
                "completed_at": t["completed_at"],
                "result": t["result"],
                "criteria": t["criteria"],
                "has_criteria": bool(t["criteria"]),
            }
            for t in tickets
        ]
        print(json.dumps(out, indent=2))
        return
    print(f"Completion audit — last {args.days} day(s): {len(tickets)} closed ticket(s)\n")
    auditable = [t for t in tickets if t["criteria"]]
    no_criteria = [t for t in tickets if not t["criteria"]]
    if auditable:
        print(f"Auditable ({len(auditable)} with criteria):")
        for t in auditable:
            print(f"  [{t['completed_at'][:10]}] {t['id']} — {t['title']}")
            print(f"    criteria: {t['criteria'][:120]}{'…' if len(t['criteria']) > 120 else ''}")
            if t["result"]:
                print(f"    result:   {t['result']}")
            print()
    if no_criteria:
        print(f"Cannot audit — no criteria ({len(no_criteria)} ticket(s)):")
        for t in no_criteria:
            print(f"  [{t['completed_at'][:10]}] {t['id']} — {t['title']}")


def cmd_log_result(args) -> None:
    log_result(args.ticket_id, args.verdict, " ".join(args.reason))


def cmd_summary(args) -> None:
    results = read_results(days=args.days)
    if not results:
        print(f"No audit results in the last {args.days} day(s).")
        return
    counts = {"pass": 0, "fail": 0, "cannot-verify": 0}
    for r in results:
        counts[r.get("verdict", "cannot-verify")] = counts.get(r.get("verdict", "cannot-verify"), 0) + 1
    print(f"Completion audit summary — last {args.days} day(s): {len(results)} result(s)")
    print(f"  pass: {counts['pass']}  fail: {counts['fail']}  cannot-verify: {counts['cannot-verify']}")
    failures = [r for r in results if r.get("verdict") == "fail"]
    if failures:
        print(f"\nFAILURES ({len(failures)}):")
        for r in failures:
            print(f"  [{r['ts'][:10]}] {r['ticket_id']}: {r['reason']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Completion audit for closed tickets")
    sub = parser.add_subparsers(dest="cmd")

    p_list = sub.add_parser("list", help="List closed tickets for audit")
    p_list.add_argument("--days", type=int, default=1)
    p_list.add_argument("--ticket", default=None)
    p_list.add_argument("--json", action="store_true")

    p_log = sub.add_parser("log-result", help="Record an audit verdict")
    p_log.add_argument("ticket_id")
    p_log.add_argument("verdict", choices=["pass", "fail", "cannot-verify"])
    p_log.add_argument("reason", nargs="+")

    p_sum = sub.add_parser("summary", help="Show recent audit results")
    p_sum.add_argument("--days", type=int, default=7)

    args = parser.parse_args()
    if args.cmd == "list":
        cmd_list(args)
    elif args.cmd == "log-result":
        cmd_log_result(args)
    elif args.cmd == "summary":
        cmd_summary(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
