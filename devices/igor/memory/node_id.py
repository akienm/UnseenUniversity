"""
node_id.py — Timestamp-based node ID generator (D256).

Format: YYYYMMDDHHMMSSuuuuuu[.swarm_name[.instance_name[.coe_name]]]
  - YYYYMMDDHHMMSSuuuuuu  — UTC datetime with microseconds (20 chars)
  - .swarm_name            — appended when IGOR_SWARM_NAME is set (or >1 machine in cluster)
  - .instance_name         — appended when IGOR_INSTANCE_ID differs from default
  - .coe_name              — appended when IGOR_COE_NAME is set (future: multi-COA support)

Serial calving: a module-level counter prevents microsecond collisions within the same
process. Under burst conditions (multiple nodes created in the same microsecond),
the counter increments so every ID is unique without sleeping.

Registry: every generated ID is registered in node_registry (Postgres) and cached
in Redis if available.

Forensic log: ~/.TheIgors/logs/node_registry.log
"""

import os
import socket
import threading
import time
from ..paths import paths as _paths
from datetime import datetime, timezone
from pathlib import Path

# ── Suffix resolution ─────────────────────────────────────────────────────────

_DEFAULT_INSTANCE = "Igor-wild-0001"


def _swarm_name() -> str | None:
    """Return IGOR_SWARM_NAME env var, or socket hostname as fallback if set."""
    name = os.getenv("IGOR_SWARM_NAME", "")
    if name:
        return name
    # Use hostname only if it looks like a known swarm node (non-generic)
    host = socket.gethostname().lower().replace("-", "").replace("_", "")
    # Only embed if it's explicitly a multi-machine setup
    explicit = os.getenv("IGOR_MULTI_MACHINE", "")
    if explicit:
        return socket.gethostname()
    return None


def _instance_name() -> str | None:
    """Return instance suffix if non-default."""
    iid = os.getenv("IGOR_INSTANCE_ID", _DEFAULT_INSTANCE)
    if iid != _DEFAULT_INSTANCE:
        return iid
    return None


def _coe_name() -> str | None:
    """Return COE (Center of Attention) name if set (future multi-COA support)."""
    return os.getenv("IGOR_COE_NAME") or None


def build_suffix() -> str:
    """Return the dotted suffix string for this process context. May be empty."""
    parts = []
    s = _swarm_name()
    if s:
        parts.append(s)
        i = _instance_name()
        if i:
            parts.append(i)
            c = _coe_name()
            if c:
                parts.append(c)
    return ("." + ".".join(parts)) if parts else ""


# ── Serial calving counter ────────────────────────────────────────────────────
# Prevents microsecond collisions within the same process.
# Each call within the same microsecond bumps this counter, producing a unique ID.

_lock = threading.Lock()
_last_us: int = 0  # last microsecond timestamp used
_seq: int = 0  # sequence within that microsecond


def _next_unique_us() -> int:
    """Return a microsecond timestamp guaranteed unique within this process."""
    global _last_us, _seq
    with _lock:
        now_us = int(datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"))
        if now_us > _last_us:
            _last_us = now_us
            _seq = 0
        else:
            _seq += 1
            # Increment the microsecond field to make ID unique
            _last_us += 1
        return _last_us


# ── Public API ────────────────────────────────────────────────────────────────


def new_node_id(suffix: str | None = None) -> str:
    """
    Generate a new globally unique node ID.

    Args:
        suffix: Override the auto-detected suffix. Pass "" to suppress all suffixes.
                Pass None (default) to use the process-level suffix from env vars.

    Returns:
        e.g. "20260329143022123456" or "20260329143022123456.akiendelllinux"
    """
    us = _next_unique_us()
    ts = str(us)  # already 20 digits: YYYYMMDDHHMMSSuuuuuu
    sfx = build_suffix() if suffix is None else (("." + suffix) if suffix else "")
    node_id = ts + sfx
    _log_generation(node_id)
    return node_id


def ts_from_datetime(dt: datetime) -> str:
    """
    Convert a datetime to the 20-char timestamp portion of a node ID.
    Used by the migration script to generate IDs from historical dates.
    """
    return dt.strftime("%Y%m%d%H%M%S%f")


def parse_node_id(node_id: str) -> dict:
    """
    Parse a node ID into its components.

    Returns dict with keys: timestamp_str, datetime (UTC), swarm, instance, coe.
    Returns empty dict if format doesn't match.
    """
    parts = node_id.split(".")
    ts_str = parts[0]
    if len(ts_str) != 20 or not ts_str.isdigit():
        return {}
    try:
        dt = datetime.strptime(ts_str, "%Y%m%d%H%M%S%f").replace(tzinfo=timezone.utc)
    except ValueError:
        return {}
    result = {
        "timestamp_str": ts_str,
        "datetime": dt,
        "swarm": None,
        "instance": None,
        "coe": None,
    }
    if len(parts) > 1:
        result["swarm"] = parts[1]
    if len(parts) > 2:
        result["instance"] = parts[2]
    if len(parts) > 3:
        result["coe"] = parts[3]
    return result


# ── Registry write ────────────────────────────────────────────────────────────


def register_node(
    node_id: str,
    table_name: str,
    row_id: str,
    machine_id: str | None = None,
    migrated_from: str | None = None,
    db_url: str | None = None,
) -> None:
    """
    Write node_id to node_registry (Postgres) and warm the Redis cache.
    Non-fatal — logs errors but never raises.
    """
    _registry_pg(node_id, table_name, row_id, machine_id, migrated_from, db_url)
    _registry_redis(node_id, table_name, row_id)


def _get_db_url() -> str:
    return _paths().home_db_url


def _registry_pg(
    node_id: str,
    table_name: str,
    row_id: str,
    machine_id: str | None,
    migrated_from: str | None,
    db_url: str | None,
) -> None:
    try:
        import psycopg2

        url = db_url or _get_db_url()
        conn = psycopg2.connect(url)
        conn.autocommit = True
        parsed = parse_node_id(node_id)
        created_at = parsed.get("datetime")

        # Skip registration for non-timestamp IDs (fixture nodes like PROC_TRAINING_PASS)
        if created_at is None:
            conn.close()
            return

        mach = machine_id or _swarm_name() or socket.gethostname()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO node_registry (node_id, table_name, row_id, machine_id, created_at, migrated_from)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (node_id) DO NOTHING
                """,
                (node_id, table_name, row_id, mach, created_at, migrated_from),
            )
        conn.close()
    except Exception as e:
        _log_error(f"registry_pg failed node_id={node_id}: {e}")


def _registry_redis(node_id: str, table_name: str, row_id: str) -> None:
    try:
        import redis as _redis

        r = _get_redis()
        if r is None:
            return
        import json

        r.setex(
            f"node:{node_id}",
            7 * 24 * 3600,  # 7-day TTL
            json.dumps({"table_name": table_name, "row_id": row_id}),
        )
    except Exception as e:
        _log_error(f"registry_redis failed node_id={node_id}: {e}")


# ── Registry lookup ───────────────────────────────────────────────────────────


def node_locate(node_id: str, db_url: str | None = None) -> dict | None:
    """
    Return {"table_name": ..., "row_id": ...} for a node_id, or None if not found.
    Checks Redis first, falls back to Postgres.
    """
    # Redis fast path
    try:
        r = _get_redis()
        if r is not None:
            import json

            val = r.get(f"node:{node_id}")
            if val:
                return json.loads(val)
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le
        _le(kind="SILENT_EXCEPT", detail=f"node_id.py:255: {_exc}")

    # Postgres fallback
    try:
        import psycopg2

        url = db_url or _get_db_url()
        conn = psycopg2.connect(url)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name, row_id FROM node_registry WHERE node_id=%s",
                (node_id,),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            result = {"table_name": row[0], "row_id": row[1]}
            # Warm Redis cache on Postgres hit
            _registry_redis(node_id, row[0], row[1])
            return result
    except Exception as e:
        _log_error(f"node_locate pg failed node_id={node_id}: {e}")
    return None


def node_exists(node_id: str, db_url: str | None = None) -> bool:
    """Return True if node_id is in the registry."""
    return node_locate(node_id, db_url) is not None


# ── Redis client ──────────────────────────────────────────────────────────────

_redis_client = None
_redis_checked = False
_redis_lock = threading.Lock()


def _get_redis():
    """Return a Redis client, or None if Redis is unavailable. Cached per process."""
    global _redis_client, _redis_checked
    with _redis_lock:
        if _redis_checked:
            return _redis_client
        _redis_checked = True
        try:
            import redis as _redis

            host = os.getenv("IGOR_REDIS_HOST", "127.0.0.1")
            port = int(os.getenv("IGOR_REDIS_PORT", "6379"))
            r = _redis.Redis(
                host=host, port=port, decode_responses=True, socket_connect_timeout=1
            )
            r.ping()
            _redis_client = r
        except Exception:
            _redis_client = None
    return _redis_client


# ── Forensic logging ──────────────────────────────────────────────────────────

_LOG_DIR = Path.home() / ".TheIgors" / "logs"
_LOG_FILE = _LOG_DIR / "node_registry.log"


def _log_generation(node_id: str) -> None:
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        with open(_LOG_FILE, "a") as f:
            f.write(f"{ts}  GEN  {node_id}\n")
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le
        _le(kind="SILENT_EXCEPT", detail=f"node_id.py:327: {_exc}")


def _log_error(msg: str) -> None:
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat()
        with open(_LOG_FILE, "a") as f:
            f.write(f"{ts}  ERR  {msg}\n")
    except Exception as _exc:
        from ..cognition.forensic_logger import log_error as _le
        _le(kind="SILENT_EXCEPT", detail=f"node_id.py:337: {_exc}")
