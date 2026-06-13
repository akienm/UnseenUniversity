"""Channel tools — read/write the shared inter-agent message channel."""

from __future__ import annotations

import json
import os
import ssl
import urllib.request

import psycopg2
import psycopg2.extras

_PG_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)
_CC_SEND_URL = os.environ.get("CC_SEND_URL", "http://localhost:8082/api/cc_send")

SCHEMAS = [
    {
        "name": "channel_read",
        "description": (
            "Read recent messages from a named channel. "
            "Defaults to the 'shared' channel (CC + Igor + Akien + agents). "
            "Pass since_id to get only messages newer than a known message ID."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "channel": {
                    "type": "string",
                    "description": "Channel name to read from (default 'shared')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max messages to return (default 20)",
                },
                "since_id": {
                    "type": "integer",
                    "description": "Return only messages with id > since_id (optional)",
                },
                "author": {
                    "type": "string",
                    "description": "Filter by author, e.g. 'igor' (optional)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "channel_send",
        "description": (
            "Send a message to a named channel. "
            "Defaults to 'shared' — the common channel where Igor, Claude, "
            "and Akien converse. Igor processes messages with author 'claude-code'."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Message to send"},
                "channel": {
                    "type": "string",
                    "description": "Target channel name (default 'shared')",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "cc_send",
        "description": (
            "DEPRECATED: use channel_send instead. "
            "Sends to the 'shared' channel. Kept as alias for backward compatibility."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Message to send to Igor"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "request_compaction",
        "description": (
            "Queue a /compact of the CC session. Writes the preserve string to "
            "~/.unseen_university/cc_compact_pending.txt; the UserPromptSubmit hook fires "
            "it on the next turn. Called by /savestate to trigger compaction."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "preserve_instructions": {
                    "type": "string",
                    "description": "Preserve string to pass to /compact.",
                },
            },
            "required": [],
        },
    },
]


def _q(sql: str, params=(), pg_url: str = _PG_URL) -> list[dict]:
    with psycopg2.connect(pg_url) as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]


def channel_read(
    channel: str = "shared",
    limit: int = 20,
    since_id: int | None = None,
    author: str | None = None,
    pg_url: str = _PG_URL,
) -> str:
    params: list = []
    where_parts = ["channel = %s"]
    params.append(channel)
    if since_id is not None:
        where_parts.append("id > %s")
        params.append(since_id)
    if author:
        where_parts.append("author = %s")
        params.append(author)
    where = "WHERE " + " AND ".join(where_parts)
    params.append(limit)
    rows = _q(
        f"SELECT id, ts, author, content FROM channel_messages "
        f"{where} ORDER BY id DESC LIMIT %s",
        params,
        pg_url,
    )
    if not rows:
        return "No messages found."
    rows = list(reversed(rows))
    lines = [f"{len(rows)} messages (newest last):"]
    for r in rows:
        ts = (r["ts"] or "")[-8:]
        content = (r["content"] or "").strip()
        lines.append(f"[id={r['id']} {ts}] {r['author']}: {content}")
    return "\n".join(lines)


def channel_send(
    content: str, channel: str = "shared", cc_send_url: str = _CC_SEND_URL
) -> str:
    payload = json.dumps({"content": content, "session_id": channel}).encode()
    req = urllib.request.Request(
        cc_send_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as r:
            resp = json.loads(r.read())
            return f"Sent to '{channel}'. Response: {resp}"
    except Exception as e:
        return f"channel_send failed: {e}"


def _request_compaction(preserve_instructions: str) -> str:
    """Write preserve string to cc_compact_pending.txt; hook fires it on next turn."""
    from pathlib import Path

    compact_file = Path.home() / ".unseen_university" / "cc_compact_pending.txt"
    try:
        compact_file.parent.mkdir(parents=True, exist_ok=True)
        compact_file.write_text(preserve_instructions)
        return f"Compact queued → {compact_file}. Will fire on next turn."
    except Exception as exc:
        return f"ERROR writing compact pending file: {exc}"


def dispatch(
    name: str, args: dict, pg_url: str = _PG_URL, cc_send_url: str = _CC_SEND_URL
) -> str | None:
    _handlers = {
        "channel_read":       lambda a: channel_read(
            a.get("channel", "shared"), a.get("limit", 20), a.get("since_id"), a.get("author"), pg_url,
        ),
        "channel_send":       lambda a: channel_send(a["content"], a.get("channel", "shared"), cc_send_url),
        "cc_send":            lambda a: channel_send(a["content"], "shared", cc_send_url),
        "request_compaction": lambda a: _request_compaction(a.get("preserve_instructions", "")),
    }
    handler = _handlers.get(name)
    return handler(args) if handler else None
