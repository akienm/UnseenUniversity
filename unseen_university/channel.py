"""
channel.py — Standalone channel post utility for UU rack devices.

Writes to Postgres channel_messages (primary) + JSONL fallback.
No dependency on Igor internals — safe to import from any rack device.

Usage:
    from unseen_university.channel import post_to_channel
    post_to_channel("Granny routed T-fix-auth → igor", author="granny-weatherwax")
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_CHANNEL_MESSAGES_TABLE = "channel_messages"
_JSONL_FALLBACK = (
    Path(os.environ.get("IGOR_HOME", str(Path.home() / ".unseen_university")))
    / "cc_channel"
    / "messages.jsonl"
)

_UC_PORT = int(os.environ.get("IGOR_UC_PORT", "8082"))
_UC_BASE = os.environ.get("IGOR_UC_BASE", f"http://localhost:{_UC_PORT}")


def _ws_push(message: str, author: str, channel: str) -> None:
    """Best-effort push to web server WebSocket hub via /api/agents/{author}/send.

    Never raises — if the web server is unreachable the Postgres write already happened.
    """
    try:
        session_id = channel if channel.startswith("comms://") else f"comms://{channel}"
        body = json.dumps({"content": message, "session_id": session_id}).encode()
        req = urllib.request.Request(
            f"{_UC_BASE}/api/agents/{author}/send",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=1.5)
    except Exception:
        pass  # web server offline or unreachable — Postgres write is the durable record


def post_to_channel(
    message: str,
    author: str = "rack",
    channel: str = "shared",
) -> None:
    """Post a message to the shared channel.

    Writes to Postgres channel_messages (primary) and JSONL fallback.
    Both writes are best-effort — failures are logged at WARNING but never
    raised so the caller is never interrupted by a channel write error.

    Args:
        message: Human-readable message content.
        author: Device ID or name that produced the message.
        channel: Target channel name (default 'shared').
    """
    db_url = os.environ.get("IGOR_HOME_DB_URL", "")
    if not db_url:
        # No channel configured — skip silently. JSONL fallback is for Postgres-down,
        # not for "no DB URL set" (which is the test environment).
        return

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    pg_ok = False
    try:
        import psycopg2

        conn = psycopg2.connect(db_url)
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"INSERT INTO {_CHANNEL_MESSAGES_TABLE}"
                    " (ts, author, type, content, channel, source_agent)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (ts, author, "message", message, channel, author),
                )
        conn.close()
        pg_ok = True
    except Exception as exc:
        log.warning("channel post Postgres failed (%s): %s", author, exc)

    if not pg_ok:
        # JSONL fallback — only reached when Postgres is configured but unavailable
        try:
            fallback = _JSONL_FALLBACK
            fallback.parent.mkdir(parents=True, exist_ok=True)
            entry = json.dumps(
                {
                    "ts": ts,
                    "author": author,
                    "type": "message",
                    "content": message,
                    "channel": channel,
                },
                ensure_ascii=False,
            )
            with open(fallback, "a", encoding="utf-8") as f:
                f.write(entry + "\n")
        except Exception as exc:
            log.warning("channel post JSONL fallback failed (%s): %s", author, exc)

    _ws_push(message, author, channel)
