"""ticket_tools.py — file_ticket MCP tool for Librarian.

Writes a ticket to the filesystem ticket store (the build queue) so any device
can file tickets without importing TheIgors and without touching Postgres
(D-build-queue-filesystem-first-2026-06-19).
"""

from __future__ import annotations

import json
import re


def _slug(title: str) -> str:
    s = title.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s[:40]


def file_ticket(
    title: str,
    description: str,
    size: str = "S",
    tags: list[str] | None = None,
    decision_id: str | None = None,
    priority: float = 0.5,
    status: str = "triage",
) -> dict:
    """File a new ticket in the build queue via the filesystem ticket store.

    Filesystem-first (D-build-queue-filesystem-first-2026-06-19): ticket state is
    the dynamic queue, owned by ``ticket_store`` (atomic write+rename), not
    clan.memories. ``write`` is an upsert on ``body.id``, so re-filing the same
    title updates in place (matching the old ON CONFLICT DO UPDATE semantics).
    """
    from datetime import datetime, timezone

    from unseen_university import ticket_store
    from unseen_university.action_log import append_action

    ticket_id = f"T-{_slug(title)}"
    now = datetime.now(timezone.utc).isoformat()
    body = {
        "id": ticket_id,
        "title": title,
        "description": description,
        "size": size,
        "tags": tags or [],
        "status": status,
        "priority": priority,
        "decision_id": decision_id,
        "created_by": "librarian",
        "created_at": now,
        "updated_at": now,
    }
    ticket_store.write(body)

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
            "File a new ticket in the build queue. "
            "Writes to the filesystem ticket store (the queue's storage). "
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
