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


def post_to_channel(message: str, author: str = "igor") -> None:
    """Post a message to the Igor channel.

    Writes to Postgres channel_messages (primary) and ~/.TheIgors/cc_channel/messages.jsonl
    (fallback). Both writes are best-effort — failures are silently swallowed so the
    caller is never interrupted by a channel write error.
    """
    db_url = os.getenv(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/igor_wild_0001",
    )
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Postgres channel
    try:
        import psycopg2 as _pg

        conn_pg = _pg.connect(db_url)
        with conn_pg:
            with conn_pg.cursor() as c:
                c.execute(
                    "INSERT INTO channel_messages (ts, author, type, content) VALUES (%s, %s, %s, %s)",
                    (ts, author, "message", message),
                )
        conn_pg.close()
    except Exception:
        pass

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
    except Exception:
        pass
