"""Queue MCP server — stdio JSON-RPC 2.0.

Wire into Claude Code settings as 'datacenter':
    {
      "mcpServers": {
        "datacenter": {
          "command": "python",
          "args": ["-m", "devices.queue.mcp_server"],
          "env": {"IGOR_HOME_DB_URL": "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001"}
        }
      }
    }

This makes queue_next, queue_peek, queue_show, queue_list available as
mcp__datacenter__queue_next, etc. in Claude Code sessions.
"""

from __future__ import annotations

import json
import sys
from typing import Optional

from bus.envelope import Envelope
from bus.imap_server import IMAPServer, _TEST_MODE
from devices.queue.device import QueueDevice

_device = QueueDevice()

# ── Feeds IMAP client ─────────────────────────────────────────────────────────

_feeds_imap: Optional[IMAPServer] = None


def _get_feeds_imap() -> IMAPServer:
    global _feeds_imap
    if _feeds_imap is None:
        s = IMAPServer()
        if not _TEST_MODE:
            s.start()
        _feeds_imap = s
    return _feeds_imap


def _feeds_send_to(receiver: str, message: str) -> dict:
    imap = _get_feeds_imap()
    mailbox = f"feeds/{receiver}"
    imap.create_mailbox(mailbox)
    imap.append(
        mailbox, Envelope.now("cc", mailbox, {"message": message, "kind": "send_to"})
    )
    return {"status": "ok", "mailbox": mailbox}


def _feeds_send_feed(event: str, sender: str = "cc") -> dict:
    imap = _get_feeds_imap()
    mailbox = f"feeds/{sender}"
    imap.create_mailbox(mailbox)
    imap.append(
        mailbox, Envelope.now(sender, mailbox, {"event": event, "kind": "send_feed"})
    )
    return {"status": "ok", "mailbox": mailbox}


def _feeds_view_feed(sender: str, limit: int = 20) -> dict:
    imap = _get_feeds_imap()
    mailbox = f"feeds/{sender}"
    try:
        events = imap.fetch_recent(mailbox, limit)
    except Exception:
        return {"events": [], "count": 0, "mailbox": mailbox}
    result = [
        {
            "from": e.from_device,
            "to": e.to_device,
            "sent_at": e.sent_at,
            "payload": e.payload,
        }
        for e in events
    ]
    return {"events": result, "count": len(result), "mailbox": mailbox}


_TOOL_SCHEMAS = [
    {
        "name": "queue_next",
        "description": "Atomically return the next eligible ticket for a worker and mark it in_progress. Returns null when queue is empty or gate is tripped.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "worker": {
                    "type": "string",
                    "description": "Worker name, e.g. 'claude' or 'igor'",
                }
            },
            "required": ["worker"],
        },
    },
    {
        "name": "queue_peek",
        "description": "Return the next eligible ticket for a worker without marking it in_progress. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "worker": {
                    "type": "string",
                    "description": "Worker name, e.g. 'claude' or 'igor'",
                }
            },
            "required": ["worker"],
        },
    },
    {
        "name": "queue_show",
        "description": "Return a single ticket by ID, or null if not found.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticket_id": {
                    "type": "string",
                    "description": "Ticket ID, e.g. 'T-retire-worker-daemon-sh'",
                }
            },
            "required": ["ticket_id"],
        },
    },
    {
        "name": "queue_list",
        "description": "List tickets matching optional worker and status filters.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "worker": {
                    "type": "string",
                    "description": "Filter by worker name. Omit for all workers.",
                },
                "status": {
                    "type": "string",
                    "description": "Filter by status. Defaults to 'sprint' (ready-to-work).",
                    "default": "sprint",
                },
            },
        },
    },
    {
        "name": "send_to",
        "description": "Send a directed message to a device's feed mailbox (feeds/<receiver>).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "receiver": {
                    "type": "string",
                    "description": "Target device name, e.g. 'granny' or 'cc'",
                },
                "message": {
                    "type": "string",
                    "description": "Message text to deliver",
                },
            },
            "required": ["receiver", "message"],
        },
    },
    {
        "name": "send_feed",
        "description": "Publish an event to a sender's feed mailbox (feeds/<sender>). Default sender: 'cc'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event": {
                    "type": "string",
                    "description": "Event description to publish",
                },
                "sender": {
                    "type": "string",
                    "description": "Publishing device name. Default: 'cc'",
                },
            },
            "required": ["event"],
        },
    },
    {
        "name": "view_feed",
        "description": "Read the last N events from a device's feed mailbox (feeds/<sender>). Non-destructive.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sender": {
                    "type": "string",
                    "description": "Device whose feed to read, e.g. 'granny'",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max events to return. Default: 20",
                    "default": 20,
                },
            },
            "required": ["sender"],
        },
    },
]


def _dispatch(msg: dict) -> dict | None:
    method = msg.get("method", "")
    msg_id = msg.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "datacenter", "version": "1.0.0"},
                "capabilities": {"tools": {}},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": _TOOL_SCHEMAS},
        }

    if method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments", {})
        _handlers = {
            "queue_next":  lambda a: _device.queue_next(worker=a["worker"]),
            "queue_peek":  lambda a: _device.queue_peek(worker=a["worker"]),
            "queue_show":  lambda a: _device.queue_show(ticket_id=a["ticket_id"]),
            "queue_list":  lambda a: _device.queue_list(worker=a.get("worker"), status=a.get("status", "sprint")),
            "send_to":     lambda a: _feeds_send_to(a["receiver"], a["message"]),
            "send_feed":   lambda a: _feeds_send_feed(a["event"], a.get("sender", "cc")),
            "view_feed":   lambda a: _feeds_view_feed(a["sender"], a.get("limit", 20)),
        }
        try:
            handler = _handlers.get(name)
            result = handler(args) if handler else f"ERROR: unknown tool {name!r}"
        except Exception as exc:
            result = f"ERROR: {exc}"

        text = json.dumps(result, default=str) if result is not None else "null"
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": isinstance(result, str) and result.startswith("ERROR"),
            },
        }

    if method == "notifications/initialized":
        return None

    if msg_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return None


def serve() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            print(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": "Parse error"},
                    }
                ),
                flush=True,
            )
            continue
        response = _dispatch(msg)
        if response is not None:
            print(json.dumps(response, default=str), flush=True)


if __name__ == "__main__":
    serve()
