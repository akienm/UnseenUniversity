"""Scraps tools — ticket validation and text embedding via ScrapsDevice."""

from __future__ import annotations

import json

SCHEMAS = [
    {
        "name": "scraps_embed_text",
        "description": (
            "Compute a text embedding via ScrapsDevice. "
            "Returns {vector: [float, ...], model: str, dimension: int}. "
            "Uses OpenAI text-embedding-3-small when OPENAI_API_KEY is set; "
            "falls back to a deterministic hash-sha256-384 vector otherwise. "
            "Caller owns any DB write — this tool only computes the vector."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text to embed.",
                },
                "model": {
                    "type": "string",
                    "description": "Embedding model. Only 'auto' is currently supported (default).",
                    "default": "auto",
                },
            },
            "required": ["text"],
        },
    },
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


def scraps_embed_text(args: dict) -> str:
    text = args.get("text")
    if not isinstance(text, str) or not text:
        return json.dumps({"error": "text must be a non-empty string"})
    try:
        from unseen_university.devices.scraps.scraps_device import ScrapsDevice

        result = ScrapsDevice().embed_text(text, model=args.get("model", "auto"))
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def scraps_validate_ticket(args: dict) -> str:
    ticket = args.get("ticket")
    if not isinstance(ticket, dict):
        return json.dumps({"error": "ticket must be a JSON object"})
    try:
        from unseen_university.devices.scraps.scraps_device import ScrapsDevice

        result = ScrapsDevice().validate_ticket(ticket)
        return json.dumps(result)
    except Exception as exc:
        return json.dumps({"error": str(exc)})


def dispatch(name: str, args: dict) -> str | None:
    if name == "scraps_embed_text":
        return scraps_embed_text(args)
    if name == "scraps_validate_ticket":
        return scraps_validate_ticket(args)
    return None
