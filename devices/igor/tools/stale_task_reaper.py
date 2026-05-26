"""
stale_task_reaper.py — Auto-shelve stale TASK_SET memories (T-stale-task-reaper).

TASK_SET memories with no resolved status older than STALE_HOURS get marked
"shelved" so consolidation stops re-promoting them and NE stops arcing on them.

Observed failure mode (2026-03-29): T-book-learner-hash-lookup TASK_SET promoted
every 20min by consolidation for 195+ min, causing NE arc loop + console spam.

Called by PROC_STALE_TASK_REAPER (cognitive habit, schedule 45min).
Forensic log: ~/.TheIgors/logs/stale_task_reaper.log
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from ..paths import paths as _paths

_DB_URL = _paths().home_db_url
_STALE_HOURS = int(os.getenv("IGOR_STALE_TASK_HOURS", "2"))

_RESOLVED_STATUSES = {
    "done",
    "awaiting_validation",
    "closed",
    "shelved",
    "dismissed",
    "completed",
}

log = logging.getLogger(__name__)


def run_stale_task_reaper(db_url: str | None = None) -> dict:
    """
    Find TASK_SET memories older than STALE_HOURS with no resolved status,
    mark them shelved. Returns {"shelved": count, "ids": [...]} .
    """
    import psycopg2

    url = db_url or _DB_URL
    conn = psycopg2.connect(url)
    conn.autocommit = True
    shelved_ids = []

    try:
        with conn.cursor() as cur:
            # Find candidates: TASK_SET, old enough, status not resolved
            cur.execute(
                """
                SELECT id, metadata
                FROM memories
                WHERE memory_type = 'TASK_SET'
                  AND "timestamp" < to_char(NOW() - (%s || ' hours')::interval, 'YYYY-MM-DD"T"HH24:MI:SS')
                  AND (
                      metadata->>'status' IS NULL
                      OR metadata->>'status' NOT IN ('done','awaiting_validation','closed','shelved','dismissed','completed')
                  )
                """,
                (_STALE_HOURS,),
            )
            rows = cur.fetchall()

            for mem_id, meta_raw in rows:
                meta = (
                    meta_raw
                    if isinstance(meta_raw, dict)
                    else json.loads(meta_raw or "{}")
                )
                meta["status"] = "shelved"
                meta["shelved_by"] = "PROC_STALE_TASK_REAPER"
                meta["shelved_at"] = datetime.now(timezone.utc).isoformat()
                meta["shelved_reason"] = f"no resolution after {_STALE_HOURS}h"

                cur.execute(
                    "UPDATE memories SET metadata = %s::jsonb, updated_at = NOW() WHERE id = %s",
                    (json.dumps(meta), mem_id),
                )
                shelved_ids.append(mem_id)

    finally:
        conn.close()

    count = len(shelved_ids)
    log.info(f"RUN  shelved={count}  stale_hours={_STALE_HOURS}  ids={shelved_ids}")
    if count:
        print(f"[stale_task_reaper] shelved {count} stale TASK_SET(s): {shelved_ids}")
    else:
        print(
            f"[stale_task_reaper] no stale TASK_SETs found (threshold={_STALE_HOURS}h)"
        )

    return {"shelved": count, "ids": shelved_ids}


from devices.igor.tools.registry import Tool, registry  # noqa: E402

registry.register(
    Tool(
        name="run_stale_task_reaper",
        description=(
            "Scan TASK_SET memories older than IGOR_STALE_TASK_HOURS (default 2h) "
            "with no resolved status and mark them shelved. "
            "Called by PROC_STALE_TASK_REAPER on schedule."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=run_stale_task_reaper,
    )
)

if __name__ == "__main__":
    result = run_stale_task_reaper()
    print(result)
