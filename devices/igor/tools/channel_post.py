"""
channel_post.py — Shared utility for posting to the Igor channel (T-post-channel-utility).

Extracted from goal_continuation.py and boredom_idle.py where the function
was duplicated. Writes to Postgres channel_messages table + JSONL fallback.

T-scope-guard-echo-dedup: optional in-process dedup suppresses repeat
posts of the same dedup_key within DEDUP_WINDOW_MINUTES. Cache is
process-local — resets on Igor restart so a fresh boot gives full
visibility.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

from ..paths import paths


from ..paths import paths as _paths

log = logging.getLogger(__name__)

# ── Dedup cache (T-scope-guard-echo-dedup) ───────────────────────────────
_DEDUP_LOCK = threading.Lock()
_DEDUP_LAST_POST: dict[str, float] = {}  # dedup_key → monotonic ts of last post
DEFAULT_DEDUP_WINDOW_MINUTES = 30


def _should_suppress(dedup_key: str, window_minutes: int) -> bool:
    """Return True if the same dedup_key was posted within window_minutes.

    Side-effect: if NOT suppressed, records this post's timestamp so the
    next call within the window will be suppressed. Thread-safe.
    """
    if not dedup_key:
        return False
    now = time.monotonic()
    window_secs = window_minutes * 60
    with _DEDUP_LOCK:
        last = _DEDUP_LAST_POST.get(dedup_key)
        if last is not None and (now - last) < window_secs:
            return True
        _DEDUP_LAST_POST[dedup_key] = now
    return False


def post_to_channel(
    message: str,
    author: str = "igor",
    channel: str = "shared",
    dedup_key: str | None = None,
    dedup_window_minutes: int = DEFAULT_DEDUP_WINDOW_MINUTES,
) -> None:
    """Post a message to the Igor channel.

    Writes to Postgres channel_messages (primary) and ~/.unseen_university/cc_channel/messages.jsonl
    (fallback). Both writes are best-effort — failures are silently swallowed so the
    caller is never interrupted by a channel write error.

    If dedup_key is provided and the same key was posted within
    dedup_window_minutes, the channel write is suppressed (a NOTE-level
    entry is written to the forensic logger for audit). Silences the
    Igor echo loop observed on 2026-04-18 where repeated escalations
    on the same topic fired to channel every 10-60 min.
    """
    if dedup_key and _should_suppress(dedup_key, dedup_window_minutes):
        try:
            from ..cognition.forensic_logger import log_error as _le

            _le(
                kind="CHANNEL_POST_SUPPRESSED",
                detail=f"dedup_key={dedup_key} window={dedup_window_minutes}min msg={message[:140]}",
            )
        except Exception as e:
            log.debug("post_to_channel: forensic_logger.log_error failed: %s", e)
        return
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
