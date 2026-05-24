"""Scraps tools — ticket validation via ScrapsDevice."""

from __future__ import annotations

import json

SCHEMAS = [
    {
        "name": "scraps_validate_ticket",
        "description": (
            "Validate a ticket's content before a state transition. "
            "Rule-based checks (non-empty description, non-generic title, "
            "structured section present) plus an optional Qwen 8 fuzzy pass "
            "for short descriptions. Returns {valid, issues, validated_at}. "
            "On pass, validated_at is an ISO-8601 timestamp to stamp into "
            "the ticket metadata as scraps_validated."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket": {
                    "type": "object",
                    "description": (
                        "Ticket dict with at least 'title' and 'description' keys. "
                        "Extra fields are ignored."
                    ),
                }
            },
            "required": ["ticket"],
        },
    },
]


def scraps_validate_ticket(args: dict) -> str:
    ticket = args.get("ticket")
    if not isinstance(ticket, dict):
        return json.dumps({"error": "ticket must be a JSON object"})
    try:
        from devices.scraps.scraps_device import ScrapsDevice

        result = ScrapsDevice().validate_ticket(ticket)
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def dispatch(name: str, args: dict) -> str | None:
    if name == "scraps_validate_ticket":
        return scraps_validate_ticket(args)
    return None
