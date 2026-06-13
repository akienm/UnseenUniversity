#!/usr/bin/env python3
"""
findings.py — T-experiment-findings-log

Deposit and query experiment findings. Queryable record of what we tried
and what we learned — distinct from experiment_queue (runtime probes),
decisions_log (architectural decisions), and session records (what happened).

Usage:
    # Deposit a finding
    python3 lab/claudecode/findings.py add \
        --title "DeepSeek vs Qwen reading comparison" \
        --result "Qwen won by one unit extracted per chunk" \
        --conclusion "Use Qwen for reading extraction" \
        --by "cc,akien" \
        --tags reading,model-comparison

    # Query findings
    python3 lab/claudecode/findings.py list [--tag TAG] [--limit N]
    python3 lab/claudecode/findings.py search "deepseek"

    # Show one finding
    python3 lab/claudecode/findings.py show <id>

Requires UU_HOME_DB_URL.
"""

import argparse
import json
import os
import sys
from datetime import datetime

DB_URL = os.environ.get("UU_HOME_DB_URL") or os.environ.get("IGOR_DB_URL") or ""


def _conn():
    import psycopg2
    import psycopg2.extras

    c = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    cur = c.cursor()
    cur.execute("SET search_path TO infra, clan, instance, public")
    cur.close()
    c.commit()
    return c


def add_finding(
    title: str,
    result: str,
    hypothesis: str = "",
    method: str = "",
    conclusion: str = "",
    participants: str = "",
    evidence: list = None,
    tags: list = None,
    created_by: str = "cc",
) -> int:
    """Deposit a finding. Returns the finding ID."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO experiment_findings
          (title, hypothesis, method, result, conclusion,
           participants, evidence, tags, created_at, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            title,
            hypothesis or "",
            method or "",
            result,
            conclusion or "",
            participants or "",
            json.dumps(evidence or []),
            json.dumps(tags or []),
            datetime.now().isoformat(),
            created_by,
        ),
    )
    finding_id = cur.fetchone()["id"]
    conn.commit()
    conn.close()
    return finding_id


def list_findings(tag: str = "", limit: int = 20) -> list:
    """List recent findings, optionally filtered by tag."""
    conn = _conn()
    cur = conn.cursor()
    if tag:
        cur.execute(
            "SELECT * FROM experiment_findings "
            "WHERE tags @> %s::jsonb "
            "ORDER BY id DESC LIMIT %s",
            (json.dumps([tag]), limit),
        )
    else:
        cur.execute(
            "SELECT * FROM experiment_findings ORDER BY id DESC LIMIT %s",
            (limit,),
        )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def search_findings(query: str, limit: int = 20) -> list:
    """Search findings by title, result, or conclusion text."""
    conn = _conn()
    cur = conn.cursor()
    pattern = f"%{query}%"
    cur.execute(
        "SELECT * FROM experiment_findings "
        "WHERE title ILIKE %s OR result ILIKE %s OR conclusion ILIKE %s "
        "ORDER BY id DESC LIMIT %s",
        (pattern, pattern, pattern, limit),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_finding(finding_id: int) -> dict:
    """Get a single finding by ID."""
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM experiment_findings WHERE id = %s", (finding_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


def _fmt(finding: dict) -> str:
    """Format a finding for display."""
    lines = [
        f"[{finding['id']}] {finding['title']}",
        f"  Result: {finding['result']}",
    ]
    if finding.get("conclusion"):
        lines.append(f"  Conclusion: {finding['conclusion']}")
    if finding.get("participants"):
        lines.append(f"  By: {finding['participants']}")
    tags = finding.get("tags", [])
    if isinstance(tags, str):
        tags = json.loads(tags) if tags else []
    if tags:
        lines.append(f"  Tags: {', '.join(tags)}")
    lines.append(f"  Created: {finding['created_at'][:16]} by {finding['created_by']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Experiment findings log")
    sub = parser.add_subparsers(dest="cmd")

    p_add = sub.add_parser("add", help="Deposit a finding")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--result", required=True)
    p_add.add_argument("--hypothesis", default="")
    p_add.add_argument("--method", default="")
    p_add.add_argument("--conclusion", default="")
    p_add.add_argument("--by", default="cc", help="Participants (comma-separated)")
    p_add.add_argument("--tags", default="", help="Tags (comma-separated)")
    p_add.add_argument("--evidence", default="", help="Evidence refs (comma-separated)")

    p_list = sub.add_parser("list", help="List recent findings")
    p_list.add_argument("--tag", default="")
    p_list.add_argument("--limit", type=int, default=20)

    p_search = sub.add_parser("search", help="Search findings")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=20)

    p_show = sub.add_parser("show", help="Show one finding")
    p_show.add_argument("id", type=int)

    args = parser.parse_args()

    if not DB_URL:
        print("UU_HOME_DB_URL not set")
        sys.exit(1)

    if args.cmd == "add":
        tags = (
            [t.strip() for t in args.tags.split(",") if t.strip()] if args.tags else []
        )
        evidence = (
            [e.strip() for e in args.evidence.split(",") if e.strip()]
            if args.evidence
            else []
        )
        fid = add_finding(
            title=args.title,
            result=args.result,
            hypothesis=args.hypothesis,
            method=args.method,
            conclusion=args.conclusion,
            participants=args.by,
            evidence=evidence,
            tags=tags,
        )
        print(f"Finding #{fid} deposited.")

    elif args.cmd == "list":
        for f in list_findings(tag=args.tag, limit=args.limit):
            print(_fmt(f))
            print()

    elif args.cmd == "search":
        results = search_findings(args.query, limit=args.limit)
        if not results:
            print("No findings match.")
        for f in results:
            print(_fmt(f))
            print()

    elif args.cmd == "show":
        f = get_finding(args.id)
        if not f:
            print(f"Finding #{args.id} not found.")
        else:
            print(_fmt(f))
            if f.get("hypothesis"):
                print(f"  Hypothesis: {f['hypothesis']}")
            if f.get("method"):
                print(f"  Method: {f['method']}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
