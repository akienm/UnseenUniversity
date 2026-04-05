"""
inhibition_seeder.py — T-inhibition-habit-seeder

Retrofits twm_ttl_seconds (refractory period) onto existing code_ref habits
that are missing it, preventing them from re-firing within their TTL window.

The "inhibition chain" is the basal_ganglia refractory mechanism:
  _refractory_map[habit_id] = time.time() + ttl_seconds
which suppresses the habit for that many seconds after it fires.

Spec fields:
  habit_id       — PROCEDURAL memory ID
  ttl_seconds    — refractory period (how long to suppress after firing)
  result_format  — hint for log formatting (str/dict/none)
  code_ref       — tool function being called (for documentation)
  status         — pending → live | failed:<reason>

Probe: after seeding, re-reads the memory and asserts ttl_seconds was written.
Marks status=live on success, status=failed|<reason> on failure.

run_inhibition_seed_pass() is also registered as a tool for Igor to call.
"""

import logging
import os

from .registry import Tool, registry

logger = logging.getLogger(__name__)

DB_URL = os.environ.get(
    "IGOR_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/igor_wild_0001",
)

# ── Seed spec ─────────────────────────────────────────────────────────────────
# Habits with code_ref but missing twm_ttl_seconds — refractory period needed
# to prevent rapid re-firing. TTLs chosen per habit's natural cooldown period.

INHIBITION_SPEC = [
    {
        "habit_id": "PROC_GOAL_CONTINUATION",
        "ttl_seconds": 300,
        "result_format": "str",
        "code_ref": "tools.goal_continuation:run_goal_continuation",
        "status": "pending",
    },
    {
        "habit_id": "PROC_BOREDOM_TRIGGER",
        "ttl_seconds": 1800,
        "result_format": "str",
        "code_ref": "tools.boredom_idle:run_boredom_check",
        "status": "pending",
    },
    {
        "habit_id": "PROC_SELF_TRAINING",
        "ttl_seconds": 3600,
        "result_format": "str",
        "code_ref": "tools.self_trainer:run_self_training_pass",
        "status": "pending",
    },
    {
        "habit_id": "PROC_STALE_TASK_REAPER",
        "ttl_seconds": 600,
        "result_format": "str",
        "code_ref": "tools.stale_task_reaper:run_stale_task_reaper",
        "status": "pending",
    },
    {
        "habit_id": "PROC_GIT_AUTH_CHECK",
        "ttl_seconds": 300,
        "result_format": "str",
        "code_ref": "git_auth_check:check_gh_auth",
        "status": "pending",
    },
    {
        "habit_id": "PROC_MEMORY_COUNT_SNAPSHOT",
        "ttl_seconds": 3600,
        "result_format": "str",
        "code_ref": "tools.memory_snapshot:run_memory_snapshot",
        "status": "pending",
    },
    {
        "habit_id": "PROC_DISK_USAGE_CHECK",
        "ttl_seconds": 600,
        "result_format": "str",
        "code_ref": "tools/filesystem.py:check_disk_usage",
        "status": "pending",
    },
    {
        "habit_id": "PROC_QUEUE_DRAIN",
        "ttl_seconds": 300,
        "result_format": "str",
        "code_ref": "ops:adopt_top_queue_ticket",
        "status": "pending",
    },
    {
        "habit_id": "PROC_CHUNK_INSPECTOR",
        "ttl_seconds": 1800,
        "result_format": "str",
        "code_ref": "habit_chunker:run_habit_chunking",
        "status": "pending",
    },
    {
        "habit_id": "PROC_FLUSH_HABIT_CACHE",
        "ttl_seconds": 300,
        "result_format": "str",
        "code_ref": "ops:flush_habit_cache",
        "status": "pending",
    },
]


# ── Seeder ────────────────────────────────────────────────────────────────────


def _seed_one(conn, entry: dict) -> tuple[bool, str]:
    """
    Seed a single habit entry. Returns (success, message).
    Phase 1: write twm_ttl_seconds. Phase 2: probe (re-read + verify).
    """
    habit_id = entry["habit_id"]
    ttl = entry["ttl_seconds"]

    try:
        cur = conn.cursor()

        # Phase 1: write twm_ttl_seconds into metadata (merge, preserve existing keys)
        cur.execute(
            "UPDATE memories SET metadata = metadata || %s::jsonb "
            "WHERE id = %s AND memory_type = 'PROCEDURAL'",
            [f'{{"twm_ttl_seconds": {ttl}}}', habit_id],
        )
        if cur.rowcount == 0:
            return False, f"habit {habit_id!r} not found in PROCEDURAL memories"

        conn.commit()

        # Phase 2: probe — re-read and assert ttl was written
        cur.execute(
            "SELECT metadata->>'twm_ttl_seconds' FROM memories WHERE id = %s",
            [habit_id],
        )
        row = cur.fetchone()
        if row is None:
            return False, f"habit {habit_id!r} disappeared after write"
        written_ttl = row[0]
        if written_ttl != str(ttl):
            return False, f"probe failed: expected ttl={ttl}, got {written_ttl!r}"

        return True, f"seeded ttl={ttl}s, probe=OK"

    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, f"error: {exc}"


def run_inhibition_seed_pass(**_) -> str:
    """
    Iterate INHIBITION_SPEC, seed twm_ttl_seconds onto each habit, probe.
    Idempotent — skips habits already seeded (overwrites with same value is safe).
    Returns summary of seeded/failed counts.
    """
    try:
        import psycopg2

        conn = psycopg2.connect(DB_URL)
    except Exception as exc:
        return f"[inhibition_seeder] DB connect failed: {exc}"

    seeded = 0
    failed = 0
    results = []

    for entry in INHIBITION_SPEC:
        ok, msg = _seed_one(conn, entry)
        entry["status"] = "live" if ok else f"failed|{msg}"
        if ok:
            seeded += 1
            logger.info("inhibition_seeder: %s — %s", entry["habit_id"], msg)
        else:
            failed += 1
            logger.warning("inhibition_seeder: %s FAILED — %s", entry["habit_id"], msg)
        results.append(f"  {'✓' if ok else '✗'} {entry['habit_id']}: {msg}")

    conn.close()

    summary = f"[inhibition_seeder] seeded={seeded} failed={failed}\n" + "\n".join(
        results
    )
    return summary


# ── Tool registration ─────────────────────────────────────────────────────────

registry.register(
    Tool(
        name="run_inhibition_seed_pass",
        description=(
            "T-inhibition-habit-seeder: Retrofit twm_ttl_seconds (refractory period) "
            "onto code_ref habits that are missing it. Probes each write. "
            "Idempotent. Returns seeded/failed summary."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=run_inhibition_seed_pass,
    )
)
