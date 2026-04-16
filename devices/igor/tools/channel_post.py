"""
channel_post.py — Shared utility for posting to the Igor channel (T-post-channel-utility).

Extracted from goal_continuation.py and boredom_idle.py where the function
was duplicated. Writes to Postgres channel_messages table + JSONL fallback.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from ..paths import paths


from ..paths import paths as _paths


def post_to_channel(
    message: str, author: str = "igor", channel: str = "shared"
) -> None:
    """Post a message to the Igor channel.

    Writes to Postgres channel_messages (primary) and ~/.TheIgors/cc_channel/messages.jsonl
    (fallback). Both writes are best-effort — failures are silently swallowed so the
    caller is never interrupted by a channel write error.
    """
    db_url = _paths().home_db_url
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Postgres channel
    try:
        import psycopg2 as _pg

        conn_pg = _pg.connect(db_url)
        with conn_pg:
            with conn_pg.cursor() as c:
                c.execute(
                    "INSERT INTO channel_messages (ts, author, type, content, channel) VALUES (%s, %s, %s, %s, %s)",
                    (ts, author, "message", message, channel),
                )
        conn_pg.close()
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le
        _le(kind="SILENT_EXCEPT", detail=f"channel_post.py:44: {_exc}")

    # JSONL fallback
    try:
        channel_file = paths().cc_channel / "messages.jsonl"
        channel_file.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps(
            {"ts": ts, "author": author, "type": "message", "content": message},
            ensure_ascii=False,
        )
        with open(channel_file, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le
        _le(kind="SILENT_EXCEPT", detail=f"channel_post.py:57: {_exc}")
