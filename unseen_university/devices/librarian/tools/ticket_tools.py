"""ticket_tools.py — file_ticket MCP tool for Librarian.

Writes a cc_queue-compatible ticket directly to clan.memories so any
device can file tickets without importing TheIgors.
"""

from __future__ import annotations

import json
import os
import re

_PG_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)
_TICKETS_ROOT = "TICKETS_ROOT"


def _slug(title: str) -> str:
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:40]


def _conn():
    import psycopg2

    return psycopg2.connect(_PG_URL)


def file_ticket(
    title: str,
    description: str,
    size: str = "S",
    tags: list[str] | None = None,
    decision_id: str | None = None,
    priority: float = 0.5,
    status: str = "triage",
) -> dict:
    """File a new ticket in cc_queue via direct clan.memories insert."""
    from datetime import datetime, timezone

    from unseen_university.action_log import append_action

    ticket_id = f"T-{_slug(title)}"
    now = datetime.now(timezone.utc).isoformat()
    narrative = f"{title}\n\n{description}" if description else title
    metadata = {
        "kind": "ticket",
        "id": ticket_id,
        "title": title,
        "description": description,
        "size": size,
        "tags": tags or [],
        "status": status,
        "priority": priority,
        "decision_id": decision_id,
    }

    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO clan.memories
                  (id, narrative, memory_type, parent_id, metadata, timestamp,
                   source, scope, confidence, updated_at)
                VALUES (%s, %s, 'FACTUAL', %s, %s::jsonb, %s,
                        'cc_queue', 'class', 1.0, %s)
                ON CONFLICT (id) DO UPDATE SET
                  narrative = EXCLUDED.narrative,
                  metadata  = EXCLUDED.metadata,
                  updated_at = EXCLUDED.updated_at
                """,
                (
                    ticket_id,
                    narrative,
                    _TICKETS_ROOT,
                    json.dumps(metadata),
                    now,
                    now,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    append_action(
        "librarian",
        "file_ticket",
        {"ticket_id": ticket_id, "title": title, "size": size, "status": status},
        f"filed {ticket_id}",
    )
    return {"ticket_id": ticket_id, "title": title, "status": status}


# ── MCP wiring ────────────────────────────────────────────────────────────────

SCHEMAS: list[dict] = [
    {
        "name": "file_ticket",
        "description": (
            "File a new ticket in cc_queue. "
            "Writes directly to clan.memories (cc_queue's storage). "
            "Returns {ticket_id, title, status}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short ticket title (<80 chars)",
                },
                "description": {
                    "type": "string",
                    "description": "Problem + proposed shape + scope",
                },
                "size": {
                    "type": "string",
                    "enum": ["S", "M", "L", "XL"],
                    "description": "Ticket size estimate",
                    "default": "S",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Topic tags",
                    "default": [],
                },
                "decision_id": {
                    "type": "string",
                    "description": "Decision ID this ticket belongs to (e.g. D-foo-2026-01-01)",
                },
                "priority": {
                    "type": "number",
                    "description": "Priority 0.0–1.0",
                    "default": 0.5,
                },
                "status": {
                    "type": "string",
                    "description": "Initial status",
                    "default": "triage",
                },
            },
            "required": ["title", "description"],
        },
    }
]


def dispatch(name: str, args: dict) -> str | None:
    if name == "file_ticket":
        result = file_ticket(
            title=args["title"],
            description=args["description"],
            size=args.get("size", "S"),
            tags=args.get("tags"),
            decision_id=args.get("decision_id"),
            priority=float(args.get("priority", 0.5)),
            status=args.get("status", "triage"),
        )
        return json.dumps(result)
    return None
