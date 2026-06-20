#!/usr/bin/env python3
"""
decision_manager.py — Record a design decision atomically.

Does three things in one shot:
  1. Emits a JSON record to devlab/runtime/memory/decisions/ via memory_emit
     (T-decisions-dsb-cutover: was DSB prepend — cutover 2026-06-17)
  2. Upserts to docs_entries Postgres table (token-efficient DB mirror)
  3. Posts to cc_queue for Igor memory flush (non-fatal if Igor down)

Usage:
    python3 claudecode/decision_manager.py add D133 "session-in-db" "implemented" \
        "sessions table in Postgres; session_manager.py; sessions.md rendered from DB"
    python3 claudecode/decision_manager.py show [N]     — last N decisions from JSON store
    python3 claudecode/decision_manager.py get D133     — print one decision

Called by /decided skill at Step 2 to eliminate manual DSB editing.

Ref: D133, T-decided-habit, T-decisions-dsb-cutover
"""

import os
import sys
import subprocess
from datetime import datetime
from pathlib import Path

DB_URL = os.getenv("UU_HOME_DB_URL") or os.getenv("IGOR_DB_URL")

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
_MEMORY_ROOT = os.environ.get(
    "UU_MEMORY_ROOT", str(_REPO_ROOT / "devlab" / "runtime" / "memory")
)


# ── DB helpers ────────────────────────────────────────────────────────────────


def _conn():
    import psycopg2
    import psycopg2.extras

    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def _upsert_docs_entry(decision_id: str, line: str):
    """Upsert decision line into docs_entries (secondary — JSON store is primary)."""
    if not DB_URL:
        return
    try:
        now = datetime.now().strftime("%Y-%m-%dT%H:%M")
        with _conn() as conn:
            with conn.cursor() as c:
                c.execute(
                    """
                    INSERT INTO docs_entries (source, entry_key, entry_type, content, synced_at)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (source, entry_key) DO UPDATE SET
                        entry_type = EXCLUDED.entry_type,
                        content    = EXCLUDED.content,
                        synced_at  = EXCLUDED.synced_at
                """,
                    ("decisions_log", decision_id, "decision", line, now),
                )
            conn.commit()
    except Exception as e:
        print(f"  [warn] docs_entries upsert failed: {e}", file=sys.stderr)


def _flush_to_igor(decision_id: str, description: str):
    """Post to cc_queue for Igor memory flush. Non-fatal."""
    try:
        subprocess.run(
            [
                sys.executable,
                str(
                    Path.home()
                    / "dev"
                    / "src"
                    / "unseen_university"
                    / "devlab"
                    / "claudecode"
                    / "cc_queue.py"
                ),
                "flush_decision",
                decision_id,
                description,
            ],
            timeout=10,
            capture_output=True,
        )
    except Exception:
        pass  # Igor down is fine — JSON store + DB are durable


# ── JSON store helpers ─────────────────────────────────────────────────────────


def _emit_json(decision_id: str, short_name: str, status: str, description: str) -> str:
    """Write one decision to devlab/runtime/memory/decisions/ via memory_emit.

    AR-009: logs the interface crossing at INFO level.
    Stamp is deterministic from decision_id + today (idempotent on same-day re-run).
    """
    sys.path.insert(0, str(_HERE))
    from memory_emit import emit, stamp_for_day_only

    today = datetime.now().strftime("%Y%m%d")
    stamp = stamp_for_day_only(decision_id, today)
    body = {
        "decision_id": decision_id,
        "short_name": short_name,
        "status": status,
        "description": description,
        "line": f"{decision_id}|{short_name}|{status}|{description}",
    }
    path = emit(
        "decisions",
        "cc.0",
        body,
        kind="decision",
        namespace=[decision_id],
        links={"decisions": [decision_id]},
        stamp=stamp,
    )
    print(f"DECISION_EMIT|id={decision_id}|path={path}", file=sys.stderr)
    return path


def _list_decisions(n: int = 10) -> list[dict]:
    """Return last N decision records from the JSON store, sorted by emitted_at desc."""
    import json

    decisions_dir = Path(_MEMORY_ROOT) / "decisions"
    if not decisions_dir.exists():
        return []
    files = sorted(decisions_dir.glob("cc.0.D*.json"), reverse=True)
    results = []
    for f in files[:n * 3]:  # read a few extra to allow for non-decision records
        try:
            with open(f) as fh:
                record = json.load(fh)
            if record.get("kind") == "decision":
                results.append(record)
                if len(results) >= n:
                    break
        except Exception:
            continue
    return results


# ── Commands ──────────────────────────────────────────────────────────────────


def cmd_add(args: list[str]):
    """Add a decision: add <id> <short_name> <status> <description>"""
    if len(args) < 4:
        print(
            'Usage: decision_manager.py add <id> <short_name> <status> "<description>"'
        )
        print("  status values: implemented | defined | planned | implemented-poc")
        sys.exit(2)

    decision_id = args[0].upper()
    short_name = args[1]
    status = args[2]
    description = args[3]
    line = f"{decision_id}|{short_name}|{status}|{description}"

    # 1. Emit to JSON filesystem memory store (primary write)
    path = _emit_json(decision_id, short_name, status, description)
    print(f"JSON store: {path}")

    # 2. Upsert to docs_entries (secondary — token-efficient DB mirror)
    if DB_URL:
        _upsert_docs_entry(decision_id, line)
        print(f"docs_entries upserted: {decision_id}")
    else:
        print("  [skip] UU_HOME_DB_URL not set — DB upsert skipped")

    # 3. Flush to Igor memory (best-effort)
    _flush_to_igor(decision_id, description)
    print(f"Igor flush queued: {decision_id}")


def cmd_show(n: int = 10):
    """Show last N decisions from JSON store (or DB if JSON unavailable)."""
    records = _list_decisions(n)
    if records:
        print(f"Last {n} decisions (from JSON store):")
        for r in records:
            body = r.get("body", {})
            print(f"  {body.get('line', r.get('id', '?'))}")
        return

    if DB_URL:
        try:
            with _conn() as conn:
                with conn.cursor() as c:
                    c.execute(
                        """
                        SELECT content FROM docs_entries
                        WHERE source='decisions_log' AND entry_type='decision'
                        ORDER BY entry_key DESC LIMIT %s
                    """,
                        (n,),
                    )
                    rows = c.fetchall()
            print(f"Last {n} decisions (from DB):")
            for r in rows:
                print(f"  {r['content']}")
            return
        except Exception:
            pass

    print("No decisions found (JSON store empty, DB unavailable)")


def cmd_get(decision_id: str):
    """Print one decision by ID — checks JSON store first, then DB."""
    decision_id = decision_id.upper()
    import json as _json
    from pathlib import Path as _Path

    # Search JSON store
    decisions_dir = _Path(_MEMORY_ROOT) / "decisions"
    if decisions_dir.exists():
        for f in decisions_dir.glob(f"cc.0.{decision_id}.*.json"):
            try:
                record = _json.loads(f.read_text())
                body = record.get("body", {})
                print(f"{body.get('decision_id', decision_id)} — {body.get('short_name', '?')}")
                print(f"  status: {body.get('status', '?')}")
                print(f"  {body.get('description', '')[:200]}")
                return
            except Exception:
                pass

    if DB_URL:
        try:
            with _conn() as conn:
                with conn.cursor() as c:
                    c.execute(
                        "SELECT id, short_name, status, description, ticket_id, github_issue, notes FROM decisions WHERE id=%s",
                        (decision_id,),
                    )
                    r = c.fetchone()
            if r:
                print(f"{r['id']} — {r['short_name']}")
                print(f"  status: {r['status']}")
                print(f"  {r['description'][:200]}")
                if r["ticket_id"]:
                    print(f"  ticket: {r['ticket_id']}")
                if r["github_issue"]:
                    print(f"  github: #{r['github_issue']}")
                if r["notes"]:
                    print(f"  notes: {r['notes']}")
                return
        except Exception:
            pass

    print(f"Decision {decision_id} not found")
    sys.exit(1)


def cmd_resolve(decision_id: str, resolution: str, notes: str = ""):
    """Resolve a decision: resolve D042 ticketed|superseded|implemented|wontfix [notes]"""
    decision_id = decision_id.upper()
    valid = ("ticketed", "superseded", "implemented", "wontfix")
    if resolution not in valid:
        print(f"Resolution must be one of: {', '.join(valid)}")
        sys.exit(2)
    status = f"resolved:{resolution}"
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    try:
        with _conn() as conn:
            with conn.cursor() as c:
                c.execute(
                    "UPDATE decisions SET status=%s, resolved_at=%s, notes=%s WHERE id=%s",
                    (status, now, notes or resolution, decision_id),
                )
                if c.rowcount == 0:
                    print(f"Decision {decision_id} not found in DB")
                    sys.exit(1)
            conn.commit()
        print(f"{decision_id} → {status}")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_open():
    """Show all unresolved decisions."""
    if not DB_URL:
        print("ERROR: UU_HOME_DB_URL not set", file=sys.stderr)
        sys.exit(1)
    with _conn() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT id, short_name, status, ticket_id, github_issue "
                "FROM decisions WHERE status NOT LIKE 'resolved:%%' "
                "ORDER BY id"
            )
            rows = c.fetchall()
    print(f"{len(rows)} open decisions:")
    for r in rows:
        refs = []
        if r["ticket_id"]:
            refs.append(f"T={r['ticket_id']}")
        if r["github_issue"]:
            refs.append(f"#{r['github_issue']}")
        ref_str = f" [{', '.join(refs)}]" if refs else ""
        print(f"  {r['id']:6s} {r['status']:18s} {r['short_name']}{ref_str}")


# ── Entry point ───────────────────────────────────────────────────────────────


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"

    if cmd == "add":
        cmd_add(sys.argv[2:])
    elif cmd == "show":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        cmd_show(n)
    elif cmd == "get":
        if len(sys.argv) < 3:
            print("Usage: decision_manager.py get <id>")
            sys.exit(2)
        cmd_get(sys.argv[2])
    elif cmd == "resolve":
        if len(sys.argv) < 4:
            print(
                "Usage: decision_manager.py resolve <id> ticketed|superseded|implemented|wontfix [notes]"
            )
            sys.exit(2)
        notes = sys.argv[4] if len(sys.argv) > 4 else ""
        cmd_resolve(sys.argv[2], sys.argv[3], notes)
    elif cmd == "open":
        cmd_open()
    else:
        print(f"Unknown command: {cmd}  (add|show|get|resolve|open)")
        sys.exit(2)


if __name__ == "__main__":
    main()
