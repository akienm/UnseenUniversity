"""proposal_tools.py — propose_change / list_proposals MCP tools for Librarian.

Formal code change proposals written to adc.code_proposals. CC makes all
apply decisions — nothing here auto-applies.
"""

from __future__ import annotations

import json
import os

_PG_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)
_CC_SEND_URL = os.environ.get("CC_SEND_URL", "http://localhost:8082/api/cc_send")

_DDL = """
CREATE TABLE IF NOT EXISTS adc.code_proposals (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    file_path        TEXT        NOT NULL,
    old_snippet      TEXT        NOT NULL,
    new_snippet      TEXT        NOT NULL,
    rationale        TEXT        NOT NULL,
    related_ticket_id TEXT       NULL,
    status           TEXT        NOT NULL DEFAULT 'pending',
    proposed_by      TEXT        NOT NULL DEFAULT 'librarian',
    proposed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at      TIMESTAMPTZ NULL
)
"""


def _conn():
    import psycopg2

    return psycopg2.connect(_PG_URL)


def _ensure_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(_DDL)


def _notify_channel(file_path: str, proposal_id: str) -> None:
    import ssl
    import urllib.request

    msg = (
        f"librarian: code proposal for {file_path} — "
        f"id={proposal_id[:8]}… (see list_proposals() to review)"
    )
    payload = json.dumps({"content": msg, "session_id": "shared"}).encode()
    req = urllib.request.Request(
        _CC_SEND_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=5, context=ctx):
            pass
    except Exception:
        pass  # notification is best-effort


def propose_change(
    file_path: str,
    old_snippet: str,
    new_snippet: str,
    rationale: str,
    related_ticket_id: str | None = None,
) -> dict:
    """Insert a code proposal row and post a channel notification."""
    from unseen_university.action_log import append_action

    conn = _conn()
    try:
        with conn:
            _ensure_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO adc.code_proposals
                        (file_path, old_snippet, new_snippet, rationale,
                         related_ticket_id, proposed_by)
                    VALUES (%s, %s, %s, %s, %s, 'librarian')
                    RETURNING id::text
                    """,
                    (file_path, old_snippet, new_snippet, rationale, related_ticket_id),
                )
                proposal_id = cur.fetchone()[0]
    finally:
        conn.close()

    append_action(
        "librarian",
        "propose_change",
        {"file_path": file_path, "related_ticket_id": related_ticket_id},
        f"proposal_id={proposal_id[:8]}",
    )
    _notify_channel(file_path, proposal_id)
    return {"proposal_id": proposal_id, "file_path": file_path, "status": "pending"}


def list_proposals(status: str = "pending") -> list[dict]:
    """Return proposals matching status. Default: pending."""
    conn = _conn()
    try:
        with conn:
            _ensure_table(conn)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id::text, file_path, old_snippet, new_snippet,
                           rationale, related_ticket_id, status,
                           proposed_by, proposed_at
                    FROM adc.code_proposals
                    WHERE status = %s
                    ORDER BY proposed_at DESC
                    """,
                    (status,),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


# ── MCP wiring ────────────────────────────────────────────────────────────────

SCHEMAS: list[dict] = [
    {
        "name": "propose_change",
        "description": (
            "File a formal code change proposal for CC review. "
            "Inserts into adc.code_proposals and posts a channel notification. "
            "CC makes all apply decisions — nothing auto-applies. "
            "Returns {proposal_id, file_path, status}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "File to change"},
                "old_snippet": {
                    "type": "string",
                    "description": "Exact existing code to replace",
                },
                "new_snippet": {"type": "string", "description": "Replacement code"},
                "rationale": {
                    "type": "string",
                    "description": "Why this change is needed",
                },
                "related_ticket_id": {
                    "type": "string",
                    "description": "Related ticket ID (e.g. T-foo-bar)",
                },
            },
            "required": ["file_path", "old_snippet", "new_snippet", "rationale"],
        },
    },
    {
        "name": "list_proposals",
        "description": (
            "List code proposals by status (default: pending). "
            "Returns list of proposal dicts."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Filter by status: pending, approved, rejected",
                    "default": "pending",
                },
            },
        },
    },
]


def dispatch(name: str, args: dict) -> str | None:
    if name == "propose_change":
        result = propose_change(
            file_path=args["file_path"],
            old_snippet=args["old_snippet"],
            new_snippet=args["new_snippet"],
            rationale=args["rationale"],
            related_ticket_id=args.get("related_ticket_id"),
        )
        return json.dumps(result)
    if name == "list_proposals":
        result = list_proposals(status=args.get("status", "pending"))
        # Convert datetimes to strings for JSON
        for row in result:
            if hasattr(row.get("proposed_at"), "isoformat"):
                row["proposed_at"] = row["proposed_at"].isoformat()
        return json.dumps(result)
    return None
