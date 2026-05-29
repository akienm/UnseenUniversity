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
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_CHANNEL_MESSAGES_TABLE = "channel_messages"
_JSONL_FALLBACK = (
    Path(os.environ.get("IGOR_HOME", str(Path.home() / ".TheIgors")))
    / "cc_channel"
    / "messages.jsonl"
)


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
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db_url = os.environ.get("IGOR_HOME_DB_URL", "")

    if db_url:
        try:
            import psycopg2

            conn = psycopg2.connect(db_url)
            with conn:
                with conn.cursor() as cur:
                    cur.execute(
                        f"INSERT INTO {_CHANNEL_MESSAGES_TABLE}"
                        " (ts, author, type, content, channel)"
                        " VALUES (%s, %s, %s, %s, %s)",
                        (ts, author, "message", message, channel),
                    )
            conn.close()
            return
        except Exception as exc:
            log.warning("channel post Postgres failed (%s): %s", author, exc)

    # JSONL fallback
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
