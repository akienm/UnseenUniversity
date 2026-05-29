#!/usr/bin/env python3
"""
session_manager.py — Store and query session records in Postgres.

Sessions accumulate progressively — crash loses only the delta since last /decided.
Sessions.md is a rendered view; DB is source of truth.

Usage:
    python3 claudecode/session_manager.py start <id> <theme>
        — create partial session record at session start
    python3 claudecode/session_manager.py append-change <id> "<one-line change>"
        — accumulate key_changes as work happens (called by /decided)
    python3 claudecode/session_manager.py append-decision <id> "Dxxx"
        — accumulate decision IDs (called by /decided alongside decision_manager.py add)
    python3 claudecode/session_manager.py append-tool-output <id> "<tool: output summary>"
        — accumulate tool call summaries for crash recovery (called by PostToolUse hook)
    python3 claudecode/session_manager.py finalize <id> "<next>" "<in_flight>"
        — add synthesis fields at clean session end (optional — crash-safe without it)
    python3 claudecode/session_manager.py add <id> <theme> <decisions> <key_changes> <next> <in_flight>
        — single-shot record (used by savestate if clean end; falls back to accumulation)
    python3 claudecode/session_manager.py show [N]    — last N sessions (default 5)
    python3 claudecode/session_manager.py render      — write sessions.md from DB
    python3 claudecode/session_manager.py seed        — parse sessions.md → DB (first run)
    python3 claudecode/session_manager.py get <id>    — print one session

Ref: D133, D135, T-sessions-in-db
"""

import os
import re
import sys
from datetime import datetime
from pathlib import Path

_UU_ROOT = Path(__file__).parent.parent.parent.resolve()
SESSIONS_MD = _UU_ROOT / "lab" / "docs" / "sessions.md"
CURRENT_SESSION_FILE = (
    Path(os.getenv("IGOR_RUNTIME_ROOT", Path.home() / ".unseen_university"))
    / "cc_channel"
    / "current_session.txt"
)
DB_URL = os.getenv("IGOR_HOME_DB_URL") or os.getenv("IGOR_DB_URL")


def current_session_id() -> str:
    """Return current session ID from state file, or '' if not set."""
    try:
        return CURRENT_SESSION_FILE.read_text().strip()
    except OSError:
        return ""


def _write_current_session(sid: str):
    CURRENT_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    CURRENT_SESSION_FILE.write_text(sid + "\n")


# ── DB helpers ────────────────────────────────────────────────────────────────


def _conn():
    import psycopg2
    import psycopg2.extras

    return psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)


def _ensure_table():
    with _conn() as conn:
        with conn.cursor() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id           TEXT PRIMARY KEY,   -- e.g. "2026-03-19c"
                    theme        TEXT NOT NULL,
                    decisions    TEXT DEFAULT '',     -- comma-separated "D130, D131"
                    key_changes  TEXT DEFAULT '',     -- freeform multi-line
                    tool_outputs TEXT DEFAULT '',     -- one-line per tool call for crash recovery
                    next_session TEXT DEFAULT '',
                    in_flight    TEXT DEFAULT 'NONE',
                    created_at   TEXT
                )
            """)
            c.execute(
                """
                INSERT INTO _migrations (name, applied_at)
                VALUES ('sessions_table', %s)
                ON CONFLICT (name) DO NOTHING
            """,
                (datetime.now().isoformat(),),
            )
            # Add tool_outputs column if it doesn't exist (migration for existing tables)
            c.execute("""
                ALTER TABLE sessions
                ADD COLUMN IF NOT EXISTS tool_outputs TEXT DEFAULT ''
            """)
        conn.commit()


def _upsert(conn, s: dict):
    with conn.cursor() as c:
        c.execute(
            """
            INSERT INTO sessions (id, theme, decisions, key_changes, next_session, in_flight, created_at)
            VALUES (%(id)s, %(theme)s, %(decisions)s, %(key_changes)s,
                    %(next_session)s, %(in_flight)s, %(created_at)s)
            ON CONFLICT (id) DO UPDATE SET
                theme        = EXCLUDED.theme,
                decisions    = EXCLUDED.decisions,
                key_changes  = EXCLUDED.key_changes,
                next_session = EXCLUDED.next_session,
                in_flight    = EXCLUDED.in_flight
        """,
            s,
        )


# ── Parser ────────────────────────────────────────────────────────────────────


def _parse_sessions_md(path: Path) -> list[dict]:
    """Parse sessions.md into list of session dicts, newest-first order preserved."""
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"(?=^## Session )", text, flags=re.MULTILINE)
    sessions = []
    for block in blocks:
        block = block.strip()
        if not block.startswith("## Session "):
            continue
        m = re.match(r"## Session (\S+)", block)
        if not m:
            continue
        sid = m.group(1)

        def _field(name):
            pat = rf"\*\*{name}\*\*:\s*(.+?)(?=\n\*\*|\Z)"
            fm = re.search(pat, block, re.DOTALL)
            return fm.group(1).strip() if fm else ""

        theme = _field("Theme")
        decisions = _field("Decisions")
        next_sess = _field("Next session")
        in_flight = _field("In-flight")

        # Key changes: collect bullet lines after **Key changes**:
        kc_match = re.search(r"\*\*Key changes\*\*:\s*\n((?:- .+\n?)*)", block)
        key_changes = kc_match.group(1).strip() if kc_match else ""

        sessions.append(
            {
                "id": sid,
                "theme": theme,
                "decisions": decisions,
                "key_changes": key_changes,
                "next_session": next_sess,
                "in_flight": in_flight or "NONE",
                "created_at": sid[:10],  # date portion
            }
        )
    return sessions


# ── Commands ──────────────────────────────────────────────────────────────────


def cmd_add(args: list[str]):
    """Add one session record. Args: id theme decisions key_changes next in_flight"""
    if len(args) < 2:
        print(
            "Usage: session_manager.py add <id> <theme> [decisions] [key_changes] [next] [in_flight]"
        )
        sys.exit(2)
    sid = args[0]
    theme = args[1]
    decisions = args[2] if len(args) > 2 else ""
    key_changes = args[3] if len(args) > 3 else ""
    next_session = args[4] if len(args) > 4 else ""
    in_flight = args[5] if len(args) > 5 else "NONE"

    s = {
        "id": sid,
        "theme": theme,
        "decisions": decisions,
        "key_changes": key_changes,
        "next_session": next_session,
        "in_flight": in_flight,
        "created_at": datetime.now().strftime("%Y-%m-%d"),
    }
    with _conn() as conn:
        _upsert(conn, s)
        conn.commit()
    print(f"Session {sid} recorded.")


def cmd_start(args: list[str]):
    """Create a partial session record at session start. Args: id theme"""
    if len(args) < 2:
        print("Usage: session_manager.py start <id> <theme>")
        sys.exit(2)
    sid, theme = args[0], args[1]
    s = {
        "id": sid,
        "theme": theme,
        "decisions": "",
        "key_changes": "",
        "next_session": "",
        "in_flight": "NONE",
        "created_at": datetime.now().strftime("%Y-%m-%d"),
    }
    with _conn() as conn:
        # Only insert if not already exists — don't overwrite a richer record
        with conn.cursor() as c:
            c.execute(
                """
                INSERT INTO sessions (id, theme, decisions, key_changes, next_session, in_flight, created_at)
                VALUES (%(id)s, %(theme)s, %(decisions)s, %(key_changes)s,
                        %(next_session)s, %(in_flight)s, %(created_at)s)
                ON CONFLICT (id) DO NOTHING
            """,
                s,
            )
        conn.commit()
    _write_current_session(sid)
    print(f"Session {sid} started: {theme}")
    print(f"Session ID written to {CURRENT_SESSION_FILE}")


def cmd_append_change(args: list[str]):
    """Append one key-change line. Args: [id] change (id defaults to current_session.txt)"""
    if len(args) < 1:
        print("Usage: session_manager.py append-change [id] <change>")
        sys.exit(2)
    # If first arg looks like a session ID (date format), use it; else use state file
    if len(args) >= 2 and re.match(r"^\d{4}-\d{2}-\d{2}", args[0]):
        sid, change = args[0], args[1]
    else:
        sid = current_session_id()
        change = args[0]
        if not sid:
            print(
                "ERROR: no current session. Run session_manager.py start first.",
                file=sys.stderr,
            )
            sys.exit(1)
    if not change.startswith("- "):
        change = "- " + change
    with _conn() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                UPDATE sessions
                SET key_changes = CASE
                    WHEN key_changes = '' THEN %s
                    ELSE key_changes || E'\\n' || %s
                END
                WHERE id = %s
            """,
                (change, change, sid),
            )
            if c.rowcount == 0:
                print(
                    f"  [warn] Session {sid} not found — change not recorded",
                    file=sys.stderr,
                )
                return
        conn.commit()
    print(f"Change recorded → {sid}: {change}")


def cmd_append_decision(args: list[str]):
    """Append a decision ID. Args: [id] decision_id (id defaults to current_session.txt)"""
    if len(args) < 1:
        print("Usage: session_manager.py append-decision [id] <decision_id>")
        sys.exit(2)
    if len(args) >= 2 and re.match(r"^\d{4}-\d{2}-\d{2}", args[0]):
        sid, did = args[0], args[1].upper()
    else:
        sid = current_session_id()
        did = args[0].upper()
        if not sid:
            print(
                "ERROR: no current session. Run session_manager.py start first.",
                file=sys.stderr,
            )
            sys.exit(1)
    with _conn() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                UPDATE sessions
                SET decisions = CASE
                    WHEN decisions = '' THEN %s
                    ELSE decisions || ', ' || %s
                END
                WHERE id = %s
            """,
                (did, did, sid),
            )
            if c.rowcount == 0:
                print(
                    f"  [warn] Session {sid} not found — decision not recorded",
                    file=sys.stderr,
                )
                return
        conn.commit()
    print(f"Decision recorded → {sid}: {did}")


def cmd_append_tool_output(args: list[str]):
    """Append one tool output summary (tool name + key tokens). Args: [id] summary (id defaults to current_session.txt)"""
    if len(args) < 1:
        print("Usage: session_manager.py append-tool-output [id] <summary>")
        sys.exit(2)
    # If first arg looks like a session ID (date format), use it; else use state file
    if len(args) >= 2 and re.match(r"^\d{4}-\d{2}-\d{2}", args[0]):
        sid, summary = args[0], args[1]
    else:
        sid = current_session_id()
        summary = args[0]
        if not sid:
            print(
                "ERROR: no current session. Run session_manager.py start first.",
                file=sys.stderr,
            )
            sys.exit(1)
    with _conn() as conn:
        with conn.cursor() as c:
            c.execute(
                """
                UPDATE sessions
                SET tool_outputs = CASE
                    WHEN tool_outputs = '' THEN %s
                    ELSE tool_outputs || E'\\n' || %s
                END
                WHERE id = %s
            """,
                (summary, summary, sid),
            )
            if c.rowcount == 0:
                print(
                    f"  [warn] Session {sid} not found — tool output not recorded",
                    file=sys.stderr,
                )
                return
        conn.commit()
    print(f"Tool output recorded → {sid}: {summary}")


def cmd_finalize(args: list[str]):
    """Add next_session and in_flight to complete a session record. Args: id next in_flight"""
    if len(args) < 2:
        print("Usage: session_manager.py finalize <id> <next_session> [in_flight]")
        sys.exit(2)
    sid = args[0]
    next_session = args[1]
    in_flight = args[2] if len(args) > 2 else "NONE"
    with _conn() as conn:
        with conn.cursor() as c:
            c.execute(
                "UPDATE sessions SET next_session=%s, in_flight=%s WHERE id=%s",
                (next_session, in_flight, sid),
            )
            if c.rowcount == 0:
                print(f"  [warn] Session {sid} not found", file=sys.stderr)
                sys.exit(1)
        conn.commit()
    print(f"Session {sid} finalized. Next: {next_session}")


def cmd_show(n: int = 5):
    with _conn() as conn:
        with conn.cursor() as c:
            c.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT %s", (n,))
            rows = c.fetchall()
    if not rows:
        print("No sessions in DB. Run: session_manager.py seed")
        return
    for r in rows:
        print(f"\n## Session {r['id']}")
        print(f"  Theme: {r['theme']}")
        if r.get("decisions"):
            print(f"  Decisions: {r['decisions']}")
        if r.get("key_changes"):
            for line in r["key_changes"].splitlines():
                print(f"    {line}")
        if r.get("next_session"):
            print(f"  Next: {r['next_session']}")
        if r.get("in_flight") and r["in_flight"] != "NONE":
            print(f"  In-flight: {r['in_flight']}")


def cmd_get(sid: str):
    with _conn() as conn:
        with conn.cursor() as c:
            c.execute("SELECT * FROM sessions WHERE id=%s", (sid,))
            r = c.fetchone()
    if not r:
        print(f"Session {sid} not found")
        sys.exit(1)
    print(f"## Session {r['id']}")
    print(f"**Theme**: {r['theme']}")
    if r.get("decisions"):
        print(f"**Decisions**: {r['decisions']}")
    if r.get("key_changes"):
        print(f"**Key changes**:\n{r['key_changes']}")
    if r.get("tool_outputs"):
        print(f"**Tool outputs**:\n{r['tool_outputs']}")
    if r.get("next_session"):
        print(f"**Next session**: {r['next_session']}")
    print(f"**In-flight**: {r.get('in_flight', 'NONE')}")


def cmd_seed():
    """Parse sessions.md and upsert all sessions to DB."""
    _ensure_table()
    if not SESSIONS_MD.exists():
        print(f"Not found: {SESSIONS_MD}", file=sys.stderr)
        sys.exit(1)
    sessions = _parse_sessions_md(SESSIONS_MD)
    with _conn() as conn:
        for s in sessions:
            _upsert(conn, s)
        conn.commit()
    print(f"Seeded {len(sessions)} sessions from {SESSIONS_MD}")


def cmd_render():
    """Write sessions.md from DB (newest-first)."""
    with _conn() as conn:
        with conn.cursor() as c:
            c.execute("SELECT * FROM sessions ORDER BY id DESC")
            rows = c.fetchall()

    lines = []
    for r in rows:
        lines.append(f"## Session {r['id']}")
        lines.append(f"**Theme**: {r['theme']}")
        if r.get("decisions"):
            lines.append(f"**Decisions**: {r['decisions']}")
        kc = (r.get("key_changes") or "").strip()
        if kc:
            lines.append("**Key changes**:")
            for line in kc.splitlines():
                if not line.startswith("- "):
                    line = "- " + line
                lines.append(line)
        if r.get("next_session"):
            lines.append(f"**Next session**: {r['next_session']}")
        lines.append(f"**In-flight**: {r.get('in_flight', 'NONE')}")
        lines.append("")

    SESSIONS_MD.write_text("\n".join(lines))
    print(f"Rendered {len(rows)} sessions → {SESSIONS_MD}")


# ── Entry point ───────────────────────────────────────────────────────────────


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"

    # Commands that don't need DB
    if cmd == "current":
        sid = current_session_id()
        print(sid if sid else "(no current session)")
        return

    if not DB_URL:
        print("ERROR: IGOR_HOME_DB_URL not set", file=sys.stderr)
        sys.exit(1)

    if cmd == "start":
        cmd_start(sys.argv[2:])
    elif cmd == "append-change":
        cmd_append_change(sys.argv[2:])
    elif cmd == "append-decision":
        cmd_append_decision(sys.argv[2:])
    elif cmd == "append-tool-output":
        cmd_append_tool_output(sys.argv[2:])
    elif cmd == "finalize":
        cmd_finalize(sys.argv[2:])
    elif cmd == "add":
        cmd_add(sys.argv[2:])
    elif cmd == "show":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        cmd_show(n)
    elif cmd == "get":
        if len(sys.argv) < 3:
            print("Usage: session_manager.py get <id>")
            sys.exit(2)
        cmd_get(sys.argv[2])
    elif cmd == "seed":
        cmd_seed()
    elif cmd == "render":
        cmd_render()
    else:
        print(f"Unknown command: {cmd}  (add|show|get|seed|render)")
        sys.exit(2)


if __name__ == "__main__":
    main()
