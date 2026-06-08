#!/usr/bin/env python3
"""
Seed PROC_BOREDOM_FOREMAN habit — boredom→foreman_scan reactive loop.

T-igor-boredom-background-goals: BoredomSource fires BOREDOM_DETECTED into
TWM; _check_twm_trigger_habits picks it up and calls foreman_scan() to
check the queue and dispatch the next worker.

Usage:
  python3 devices/igor/tools/seed_boredom_foreman.py
"""

import json
import os
import sys
from pathlib import Path

_UU_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_UU_ROOT))

_DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)

_HABIT = {
    "id": "PROC_BOREDOM_FOREMAN",
    "narrative": (
        "Habit: react to BOREDOM_DETECTED in TWM, call foreman_scan "
        "to check queue and dispatch next worker."
    ),
    "metadata": {
        "why": (
            "Closes the boredom→work loop: BoredomSource fires BOREDOM_DETECTED "
            "into TWM (category=boredom_detected); _check_twm_trigger_habits picks "
            "it up and calls foreman_scan() to check the queue and launch the next "
            "worker. Without this habit the BOREDOM_DETECTED observation sat unused. "
            "T-igor-boredom-background-goals."
        ),
        "twm_trigger": "BOREDOM_DETECTED",
        "code_ref": "tools.worker_foreman:foreman_scan",
        "habit_type": "cognitive",
        "deposited_by": "cc_sprint",
        "decision_id": "T-igor-boredom-background-goals",
    },
}


def seed():
    import psycopg2
    import psycopg2.extras
    from datetime import datetime, timezone

    conn = psycopg2.connect(_DB_URL, connect_timeout=10)
    conn.autocommit = True
    cur = conn.cursor()

    meta = dict(_HABIT["metadata"])
    meta["deposited_at"] = datetime.now(timezone.utc).isoformat()

    cur.execute(
        """
        INSERT INTO memories (id, narrative, memory_type, source, scope, confidence, metadata)
        VALUES (%s, %s, 'PROCEDURAL', 'cc_sprint', 'class', 1.0, %s::jsonb)
        ON CONFLICT (id) DO UPDATE SET
          narrative = EXCLUDED.narrative,
          metadata  = EXCLUDED.metadata
        """,
        (_HABIT["id"], _HABIT["narrative"], json.dumps(meta)),
    )
    conn.close()
    print(f"Seeded {_HABIT['id']}: {meta['twm_trigger']} → {meta['code_ref']}")


if __name__ == "__main__":
    seed()
