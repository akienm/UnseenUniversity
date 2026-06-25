"""
memory_sync.py — T-memory-sync #293: swarm-wide memory synchronization.

Each Igor instance syncs its portable memories against IGOR_SWARM_DB
(the home box's Postgres). Hub-and-spoke: every box pushes local changes
to the swarm home and pulls new/updated memories from it.

Gate: IGOR_MEMORY_SYNC_ENABLED=true  (default false)
Env:  IGOR_SWARM_DB     — Postgres URL for the swarm home (the home box)
      UU_HOME_DB_URL  — this instance's local Postgres

Instance self-registration:
  On every sync, this instance upserts a SWARM_{instance_id} memory node
  into the swarm DB so the home box knows who is alive.

Scope:
  Synced:     memories WHERE portable=1
  NOT synced: twm_observations, ring_memory, tails, traces (local session state)
              interpretive_edges (follow-on ticket — need natural-key index first)

Conflict resolution:
  activation_count  → GREATEST(local, remote)
  narrative/metadata → last-write-wins via updated_at (ISO string lexicographic sort)
  new rows          → INSERT (upsert); deletions NOT synced

First run (no last_sync_at): full sync of all portable memories — bootstraps a
new box with the complete knowledge graph so Leah's Igor already knows how to read.
"""

import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from devices.igor.tools.registry import Tool, registry
from ..paths import paths

logger = logging.getLogger("igor.tools.memory_sync")

_SYNC_LOG = paths().logs / "memory_sync.log"

# Columns synced (no embedding — can be recomputed; keeps sync fast)
_SYNC_COLS = (
    "id",
    "narrative",
    "memory_type",
    "parent_id",
    "children_ids",
    "link_ids",
    "valence",
    "arousal",
    "dominance",
    "activation_count",
    "friction_history",
    "timestamp",
    "metadata",
    "portable",
    "links_weighted",
    "last_accessed",
    "source",
    "certainty",
    "context_of_encoding",
    "updated_at",
)

# execute_values requires a single %s placeholder for the VALUES block.
_UPSERT_SQL = """
    INSERT INTO memories ({cols})
    VALUES %s
    ON CONFLICT (id) DO UPDATE SET
        narrative          = CASE WHEN EXCLUDED.updated_at > COALESCE(memories.updated_at, '')
                                  THEN EXCLUDED.narrative ELSE memories.narrative END,
        metadata           = CASE WHEN EXCLUDED.updated_at > COALESCE(memories.updated_at, '')
                                  THEN EXCLUDED.metadata ELSE memories.metadata END,
        certainty          = CASE WHEN EXCLUDED.updated_at > COALESCE(memories.updated_at, '')
                                  THEN EXCLUDED.certainty ELSE memories.certainty END,
        source             = CASE WHEN EXCLUDED.updated_at > COALESCE(memories.updated_at, '')
                                  THEN EXCLUDED.source ELSE memories.source END,
        activation_count   = GREATEST(memories.activation_count, EXCLUDED.activation_count),
        updated_at         = GREATEST(COALESCE(memories.updated_at, ''),
                                      COALESCE(EXCLUDED.updated_at, ''))
""".format(cols=", ".join(_SYNC_COLS))


def _log(msg: str) -> None:
    logger.info(msg)
    try:
        _SYNC_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _SYNC_LOG.open("a") as f:
            f.write(f"{datetime.now().isoformat()} {msg}\n")
    except Exception as _bare_e:
        _log(f"bare except in devices/igor/tools/memory_sync.py: {_bare_e}")


def _pg_connect(url: str):
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = False
    return conn


def _fetch_portable(conn, since_iso: str | None, batch_size: int = 500):
    """Yield rows of portable memories in batches."""
    where = "WHERE portable = 1"
    params: list = []
    if since_iso:
        where += " AND updated_at > %s"
        params.append(since_iso)
    offset = 0
    while True:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {', '.join(_SYNC_COLS)} FROM memories {where} "
            f"ORDER BY id LIMIT %s OFFSET %s",
            params + [batch_size, offset],
        )
        rows = cur.fetchall()
        if not rows:
            break
        yield rows
        offset += len(rows)
        if len(rows) < batch_size:
            break


def _upsert_batch(conn, rows: list) -> int:
    """Upsert a batch of row dicts into memories. Returns count."""
    import json as _json
    import psycopg2.extras

    def _adapt(v):
        # jsonb columns come back as Python dicts/lists from psycopg2 — re-serialize
        return _json.dumps(v) if isinstance(v, (dict, list)) else v

    values = [tuple(_adapt(r[c]) for c in _SYNC_COLS) for r in rows]
    cur = conn.cursor()
    psycopg2.extras.execute_values(cur, _UPSERT_SQL, values, page_size=200)
    return len(values)


def _get_last_sync(swarm_instance_id: str, local_conn) -> str | None:
    """Read last_sync_at from the local SWARM node metadata."""
    cur = local_conn.cursor()
    cur.execute(
        "SELECT metadata FROM memories WHERE id = %s",
        (f"SWARM_{swarm_instance_id}",),
    )
    row = cur.fetchone()
    if not row:
        return None
    import json

    try:
        raw = row["metadata"] or "{}"
        meta = raw if isinstance(raw, dict) else json.loads(raw)
        return meta.get("last_sync_at")
    except Exception:
        return None


def _set_last_sync(swarm_instance_id: str, local_conn, ts: str) -> None:
    """Update last_sync_at in the local SWARM node."""
    import json

    cur = local_conn.cursor()
    cur.execute(
        "SELECT metadata FROM memories WHERE id = %s",
        (f"SWARM_{swarm_instance_id}",),
    )
    row = cur.fetchone()
    if not row:
        return
    try:
        raw = row["metadata"] or "{}"
        meta = raw if isinstance(raw, dict) else json.loads(raw)
        meta["last_sync_at"] = ts
        cur.execute(
            "UPDATE memories SET metadata = %s, updated_at = %s WHERE id = %s",
            (json.dumps(meta), ts, f"SWARM_{swarm_instance_id}"),
        )
        local_conn.commit()
    except Exception as e:
        logger.warning("_set_last_sync failed: %s", e)


def _register_in_swarm(instance_id: str, home_db_url: str, swarm_conn) -> None:
    """Upsert this instance's SWARM node into the swarm home DB."""
    import json

    now = datetime.now().isoformat()
    node_id = f"SWARM_{instance_id}"
    meta = json.dumps(
        {
            "instance_id": instance_id,
            "db_url": home_db_url,
            "last_seen": now,
        }
    )
    cur = swarm_conn.cursor()
    cur.execute(
        """
        INSERT INTO memories
            (id, narrative, memory_type, parent_id, metadata, portable,
             timestamp, updated_at, children_ids, link_ids, friction_history,
             links_weighted, valence, arousal, dominance, activation_count,
             source, certainty, context_of_encoding)
        VALUES (%s, %s, 'FACTUAL', 'SWARM_ROOT', %s, 0,
                %s, %s, '[]', '[]', '[]', '{}',
                0, 0, 0, 0, 'swarm_registration', 1.0, '')
        ON CONFLICT (id) DO UPDATE SET
            metadata   = EXCLUDED.metadata,
            updated_at = EXCLUDED.updated_at
        """,
        (node_id, f"swarm: instance {instance_id}", meta, now, now),
    )
    swarm_conn.commit()
    _log(f"registered instance {instance_id} in swarm home")


def sync_memories(full: str = "false") -> str:
    """
    Sync portable memories between this instance and IGOR_SWARM_DB.

    full="true"  — full sync (no time filter); use on first run / new box bootstrap.
    full="false" — incremental sync since last_sync_at (default).

    Gate: IGOR_MEMORY_SYNC_ENABLED=true.
    """
    if os.getenv("IGOR_MEMORY_SYNC_ENABLED", "false").lower() != "true":
        return "Memory sync gated off (IGOR_MEMORY_SYNC_ENABLED != true)."

    swarm_url = os.getenv("IGOR_SWARM_DB", "")
    home_url = os.getenv("UU_HOME_DB_URL", "")
    instance_id = os.getenv("IGOR_INSTANCE_ID", "wild-0001")

    if not swarm_url:
        return "IGOR_SWARM_DB not set — no swarm home configured yet."
    if not home_url:
        return "UU_HOME_DB_URL not set."

    # On swarm home itself, swarm_url == home_url — skip (no-op)
    if swarm_url.rstrip("/") == home_url.rstrip("/"):
        return "This instance IS the swarm home — no sync needed."

    do_full = str(full).lower() in ("true", "1", "yes")

    try:
        local = _pg_connect(home_url)
        swarm = _pg_connect(swarm_url)
    except Exception as e:
        return f"Connection failed: {e}"

    try:
        # Self-register in swarm
        _register_in_swarm(instance_id, home_url, swarm)

        since = None if do_full else _get_last_sync(instance_id, local)
        sync_start = datetime.now().isoformat()
        since_label = since or "beginning of time"

        pushed = pulled = 0

        # Push: local → swarm
        for batch in _fetch_portable(local, since):
            pushed += _upsert_batch(swarm, batch)
        swarm.commit()

        # Pull: swarm → local
        for batch in _fetch_portable(swarm, since):
            pulled += _upsert_batch(local, batch)
        local.commit()

        _set_last_sync(instance_id, local, sync_start)

        msg = (
            f"Sync complete (since={since_label}): " f"pushed={pushed} pulled={pulled}"
        )
        _log(msg)
        return msg

    except Exception as e:
        logger.error("sync_memories failed: %s", e)
        try:
            swarm.rollback()
            local.rollback()
        except Exception as _bare_e:
            _log(f"bare except in devices/igor/tools/memory_sync.py: {_bare_e}")
        return f"Sync error: {e}"
    finally:
        try:
            local.close()
            swarm.close()
        except Exception as _bare_e:
            _log(f"bare except in devices/igor/tools/memory_sync.py: {_bare_e}")


registry.register(
    Tool(
        name="sync_memories",
        description=(
            "Sync this Igor instance's portable memories with the swarm home (IGOR_SWARM_DB). "
            "Pushes local changes and pulls new/updated memories from the swarm. "
            "On first run use full=true to bootstrap a new box with the complete knowledge graph. "
            "Gate: IGOR_MEMORY_SYNC_ENABLED=true."
        ),
        parameters={
            "type": "object",
            "properties": {
                "full": {
                    "type": "string",
                    "description": "true = full sync (all portable memories); false = incremental since last sync (default)",
                }
            },
            "required": [],
        },
        fn=sync_memories,
    )
)
