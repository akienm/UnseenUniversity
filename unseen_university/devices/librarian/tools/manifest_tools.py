"""Manifest tools — datacenter_manifest returns opinionated task-shape → MCP tool routing map.

Both CC (CAPABILITY CHECK in sprint-ticket) and Igor (habit routing) consume
this to pick the right tool without hardcoded heuristics or reasoning overhead.
Adding a new device means adding entries here; skills and habits pick it up
automatically on next manifest read.
"""

from __future__ import annotations

import json
from pathlib import Path

from skeleton.registry import DEFAULT_REGISTRY_PATH

# Librarian has no BaseDevice subclass — self-declares its class here.
_LIBRARIAN_AGENT_CLASS = "utility"
_LIBRARIAN_DEVICE_ID = "librarian"

# task_shape → {tool, when, example}
# Ordered from most-commonly-reached for to least, to aid skimming.
_ROUTING_MAP: dict[str, dict] = {
    "db_query": {
        "tool": "db_query",
        "when": "READ-only SQL SELECT against Igor Postgres DB — prefer over psycopg2 inline",
        "example": 'db_query(sql="SELECT id, narrative FROM clan.memories WHERE id=%s", params=["BOOK_123"])',
    },
    "db_write": {
        "tool": "db_dispatch",
        "when": "INSERT/UPDATE/DELETE against Igor Postgres DB",
        "example": 'db_dispatch(sql="UPDATE clan.memories SET narrative=%s WHERE id=%s", params=["text", "ID"])',
    },
    "palace_read": {
        "tool": "memory_get",
        "when": "Read a specific palace node by path (rules, design decisions, subsystem docs)",
        "example": 'memory_get(path="unseenuniversity/rules/coding")',
    },
    "palace_search": {
        "tool": "memory_search",
        "when": "Search palace nodes by topic when exact path is unknown",
        "example": 'memory_search(query="session token storage")',
    },
    "channel_read": {
        "tool": "channel_read",
        "when": "Read recent messages from the Igor/CC shared channel",
        "example": "channel_read(limit=5)",
    },
    "channel_send": {
        "tool": "channel_send",
        "when": "Send a message to the shared channel (Igor, CC, other agents read it)",
        "example": 'channel_send(text="T-foo complete", role="cc")',
    },
    "igor_health": {
        "tool": "rack_health",
        "when": "Check health/status of all devices on the rack",
        "example": "rack_health()",
    },
    "igor_traces": {
        "tool": "traces_recent",
        "when": "Read recent Igor cognition turn traces for debugging",
        "example": "traces_recent(limit=10)",
    },
    "igor_habits": {
        "tool": "habit_list",
        "when": "List Igor's registered habits (procedural memories with code_ref)",
        "example": "habit_list()",
    },
    "summarize": {
        "tool": "summarize",
        "when": "Summarize a block of text via Librarian tier-1 model",
        "example": 'summarize(text="Long text...", style="brief")',
    },
    "research": {
        "tool": "research",
        "when": "Research a question or topic; returns a structured answer",
        "example": 'research(query="what is IMAP IDLE?", depth="shallow")',
    },
}

SCHEMAS = [
    {
        "name": "datacenter_manifest",
        "description": (
            "Returns the ADC routing manifest — an opinionated map of task-shape → MCP tool. "
            "Use this to find the right tool for a task without guessing. "
            "Set routing_only=true for a compact response (just the routing map, no tool schemas)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "routing_only": {
                    "type": "boolean",
                    "description": "Return only the routing_map (default false — also returns tool list)",
                },
                "task_shape": {
                    "type": "string",
                    "description": "Optional: return routing entry for a specific task shape only",
                },
            },
            "required": [],
        },
    }
]


def _read_device_classes(registry_path: Path | None = None) -> list[dict]:
    """Read device records from the flat-file registry; return [{device_id, agent_class, status}].

    Always includes librarian's self-declaration. Gracefully returns only librarian
    when the registry file is absent or unreadable.
    """
    librarian_entry = {
        "device_id": _LIBRARIAN_DEVICE_ID,
        "agent_class": _LIBRARIAN_AGENT_CLASS,
        "status": "online",
    }
    path = registry_path or DEFAULT_REGISTRY_PATH
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return [librarian_entry]

    devices = [librarian_entry]
    for record in raw.values():
        device_id = record.get("id") or record.get("device_id", "")
        if not device_id or device_id == _LIBRARIAN_DEVICE_ID:
            continue
        devices.append(
            {
                "device_id": device_id,
                "agent_class": record.get("agent_class", "utility"),
                "status": record.get("status", "unknown"),
            }
        )
    return devices


def datacenter_manifest(
    routing_only: bool = False,
    task_shape: str | None = None,
    _registry_path: Path | None = None,
) -> str:
    if task_shape:
        entry = _ROUTING_MAP.get(task_shape)
        return json.dumps(
            {"task_shape": task_shape, "routing": entry}
            if entry
            else {
                "task_shape": task_shape,
                "routing": None,
                "known_shapes": list(_ROUTING_MAP),
            }
        )

    result: dict = {"routing_map": _ROUTING_MAP}

    if not routing_only:
        from unseen_university.devices.librarian import tools as _tools

        result["tools"] = [
            s["name"] for s in _tools.SCHEMAS if s["name"] != "datacenter_manifest"
        ]
        result["devices"] = _read_device_classes(_registry_path)

    return json.dumps(result)


def dispatch(name: str, args: dict) -> str | None:
    if name == "datacenter_manifest":
        return datacenter_manifest(
            routing_only=args.get("routing_only", False),
            task_shape=args.get("task_shape"),
        )
    return None
