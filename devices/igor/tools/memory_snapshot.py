"""
memory_snapshot.py — Nightly memory-count snapshot tool.

Fires nightly (PROC habit gates to hour >= 22, once per day).
Records memory counts by type to ~/.TheIgors/logs/memory_count.log
for trend tracking — see if the memory base grows, stagnates, or shrinks.

T-nightly-memory-count.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from .registry import Tool, registry

log = logging.getLogger(__name__)

_STAMP_FILE = Path.home() / ".TheIgors" / "logs" / "memory_count.last_run"
_DB_URL = os.getenv(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)




def run_memory_snapshot() -> str:
    """Take a nightly snapshot of memory counts by type.

    Self-gates: only runs when local hour >= 22 AND not already run today.
    Returns a summary string.
    """
    now = datetime.now(timezone.utc)
    local_hour = datetime.now().hour  # local time for night check

    # Time gate: only run after 22:00 local
    if local_hour < 22:
        return f"memory_snapshot: skipped (hour={local_hour}, gate=22)"

    # Once-per-day gate: check last-run date
    today = now.date().isoformat()
    try:
        _STAMP_FILE.parent.mkdir(parents=True, exist_ok=True)
        if _STAMP_FILE.exists():
            last_run = _STAMP_FILE.read_text().strip()
            if last_run == today:
                return f"memory_snapshot: already ran today ({today})"
    except Exception:
        pass

    # Run the count
    try:
        import psycopg2

        conn = psycopg2.connect(_DB_URL)
        cur = conn.cursor()

        # Total count
        cur.execute("SELECT COUNT(*) FROM memories")
        total = cur.fetchone()[0]

        # Count by type
        cur.execute(
            "SELECT memory_type, COUNT(*) FROM memories GROUP BY memory_type ORDER BY COUNT(*) DESC"
        )
        by_type = {row[0]: row[1] for row in cur.fetchall()}
        conn.close()
    except Exception as exc:
        msg = f"memory_snapshot: DB error — {exc}"
        log.info(msg)
        log.warning("[memory_snapshot] %s", exc)
        return msg

    # Write stamp
    try:
        _STAMP_FILE.write_text(today)
    except Exception:
        pass

    # Log the snapshot
    record = {
        "date": today,
        "ts": now.isoformat(),
        "total": total,
        "by_type": by_type,
    }
    log.info(json.dumps(record))
    log.info("[memory_snapshot] %d total memories on %s", total, today)

    summary = f"memory_snapshot {today}: {total} total — " + ", ".join(
        f"{k}:{v}" for k, v in list(by_type.items())[:5]
    )
    return summary


registry.register(
    Tool(
        name="run_memory_snapshot",
        description=(
            "Take a nightly snapshot of memory counts by type. "
            "Self-gates: only runs after 22:00 local time, once per day. "
            "Logs to ~/.TheIgors/logs/memory_count.log for trend tracking."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=run_memory_snapshot,
    )
)
