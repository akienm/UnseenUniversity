"""
machine_manager.py — DB-backed machine registry (#342).

Single source of truth for cluster machine config.
Replaces machines.json (for routing) and machine_overrides.json.

Public API:
  get_ranked_machines()          → list[MachineRecord] sorted by inference_rank
  is_in_use(hostname)            → bool (in_use_hours window OR in_use_until override)
  set_machine_override(hostname, until=None)  → mark in-use; until=None = indefinite
  clear_machine_override(hostname)            → clear override
  resolve_alias(name)            → canonical hostname | None
  get_machine(hostname)          → MachineRecord | None

Machine priority (inference_rank):
  1  akienyoga9i   — fastest CPU, wifi
  2  akiendell     — wired, desk hours blocked 0600-1800
  3  akienyogai7   — slowest CPU, living room hours blocked 1700-2100
  4  akiendelllinux — Igor home + DB host, last resort, never blocked by hours
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

_log = logging.getLogger(__name__)

_DB_URL = os.getenv("IGOR_HOME_DB_URL", "")
if not _DB_URL:
    raise RuntimeError(
        "IGOR_HOME_DB_URL not set — machine_manager requires a Postgres connection. "
        "Set this env var at system level (not user level on Windows)."
    )

# Cache TTL — reload from DB this often
_CACHE_TTL = 60.0  # seconds

_lock = threading.Lock()
_cache: list["MachineRecord"] = []
_cache_time: float = 0.0


# ── Data class ────────────────────────────────────────────────────────────────


@dataclass
class MachineRecord:
    hostname: str
    display_name: str
    ip: Optional[str]
    os: str
    cpu: str
    ram_gb: int
    network_type: str  # wired | wifi
    status: str  # online | offline
    ollama_port: int
    ollama_model: str  # default/light model
    ollama_model_batch: Optional[str]
    inference_rank: Optional[int]
    in_use_hours: list  # [[start_hour, end_hour], ...]
    in_use_until: Optional[str]  # ISO timestamp | 'indefinite' | None
    roles: list  # igor_home, db_host, ...
    aliases: list
    ssh_enabled: bool
    ssh_user: Optional[str]
    notes: Optional[str]

    @property
    def ollama_host(self) -> str:
        if self.ip:
            return f"http://{self.ip}:{self.ollama_port}"
        return f"http://localhost:{self.ollama_port}"

    def model_for(self, call_type: str) -> str:
        """Return appropriate model name for call_type."""
        if call_type in ("extraction", "batch") and self.ollama_model_batch:
            return self.ollama_model_batch
        return self.ollama_model

    @property
    def is_local(self) -> bool:
        return self.os == "linux" and (
            not self.ip or self.ip in ("127.0.0.1", "localhost", "10.0.0.229")
        )


# ── DB helpers ────────────────────────────────────────────────────────────────

_schema_ensured = False


def _ensure_schema() -> None:
    """Create machines table if it doesn't exist (idempotent, runs once per process)."""
    global _schema_ensured
    if _schema_ensured:
        return
    import psycopg2

    try:
        conn = psycopg2.connect(_DB_URL)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS machines (
                hostname        TEXT PRIMARY KEY,
                display_name    TEXT,
                ip              TEXT,
                os              TEXT DEFAULT 'linux',
                cpu             TEXT,
                ram_gb          INTEGER,
                gpu             TEXT,
                storage         TEXT,
                hardware_model  TEXT,
                network_type    TEXT DEFAULT 'wifi',
                status          TEXT DEFAULT 'online',
                ollama_port     INTEGER DEFAULT 11434,
                ollama_model    TEXT DEFAULT 'llama3.2:1b',
                ollama_model_batch TEXT,
                inference_rank  INTEGER,
                in_use_hours    JSONB DEFAULT '[]',
                in_use_until    TEXT,
                roles           JSONB DEFAULT '[]',
                aliases         JSONB DEFAULT '[]',
                ssh_enabled     BOOLEAN DEFAULT false,
                ssh_user        TEXT DEFAULT 'Igor-wild-0001',
                notes           TEXT,
                updated_at      TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD"T"HH24:MI:SS')
            )
        """)
        conn.commit()
        conn.close()
        _schema_ensured = True
        _log.debug("[machine_manager] machines table ready")
    except Exception as exc:
        _log.error("[machine_manager] _ensure_schema failed: %s", exc)


def _fetch_machines() -> list[MachineRecord]:
    """Load all machines from DB, ordered by inference_rank."""
    _ensure_schema()
    import psycopg2

    try:
        conn = psycopg2.connect(_DB_URL)
        cur = conn.cursor()
        cur.execute("""
            SELECT hostname, display_name, ip, os, cpu, ram_gb,
                   network_type, status, ollama_port, ollama_model,
                   ollama_model_batch, inference_rank,
                   in_use_hours, in_use_until, roles, aliases,
                   ssh_enabled, ssh_user, notes
            FROM machines
            WHERE status != 'offline'
              AND inference_rank IS NOT NULL
            ORDER BY inference_rank
            """)
        rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        _log.error(
            "[machine_manager] DB_FETCH_FAIL: %s (db_url=%s)",
            exc,
            (
                _DB_URL.split("@")[-1] if "@" in _DB_URL else _DB_URL
            ),  # host/db only, no creds
        )
        return []

    if not rows:
        _log.warning(
            "[machine_manager] MACHINES_EMPTY: query returned 0 rows — "
            "check machines table has rows with status!='offline' AND inference_rank IS NOT NULL "
            "(db=%s)",
            _DB_URL.split("/")[-1] if "/" in _DB_URL else _DB_URL,
        )

    machines = []
    for row in rows:
        (
            hostname,
            display_name,
            ip,
            os_,
            cpu,
            ram_gb,
            network_type,
            status,
            ollama_port,
            ollama_model,
            ollama_model_batch,
            inference_rank,
            in_use_hours_raw,
            in_use_until,
            roles_raw,
            aliases_raw,
            ssh_enabled,
            ssh_user,
            notes,
        ) = row

        def _j(val):
            if isinstance(val, (list, dict)):
                return val
            try:
                return json.loads(val) if val else []
            except Exception:
                return []

        machines.append(
            MachineRecord(
                hostname=hostname,
                display_name=display_name or hostname,
                ip=ip,
                os=os_ or "linux",
                cpu=cpu or "",
                ram_gb=ram_gb or 0,
                network_type=network_type or "wifi",
                status=status or "online",
                ollama_port=ollama_port or 11434,
                ollama_model=ollama_model or "llama3.2:1b",
                ollama_model_batch=ollama_model_batch,
                inference_rank=inference_rank,
                in_use_hours=_j(in_use_hours_raw),
                in_use_until=in_use_until,
                roles=_j(roles_raw),
                aliases=_j(aliases_raw),
                ssh_enabled=bool(ssh_enabled),
                ssh_user=ssh_user,
                notes=notes,
            )
        )
    return machines


def _write_override(hostname: str, until: Optional[str]) -> None:
    """Write in_use_until to machines table."""
    import psycopg2

    conn = psycopg2.connect(_DB_URL)
    cur = conn.cursor()
    cur.execute(
        "UPDATE machines SET in_use_until = %s, updated_at = to_char(NOW(), 'YYYY-MM-DD\"T\"HH24:MI:SS') WHERE hostname = %s",
        (until, hostname),
    )
    conn.commit()
    conn.close()
    _invalidate_cache()


def _invalidate_cache() -> None:
    global _cache_time
    with _lock:
        _cache_time = 0.0


# ── Public API ────────────────────────────────────────────────────────────────


def get_ranked_machines() -> list[MachineRecord]:
    """Return online machines sorted by inference_rank. Cached 60s."""
    global _cache, _cache_time
    import time

    with _lock:
        now = time.monotonic()
        if _cache and (now - _cache_time) < _CACHE_TTL:
            return list(_cache)

    machines = _fetch_machines()
    with _lock:
        _cache = machines
        _cache_time = time.monotonic()
    return list(machines)


def get_machine(hostname: str) -> Optional[MachineRecord]:
    """Return a single machine by hostname (online + ranked only)."""
    for m in get_ranked_machines():
        if m.hostname == hostname:
            return m
    return None


def get_all_machines() -> list[MachineRecord]:
    """Return EVERY registered machine, including offline and unranked.

    Distinct from get_ranked_machines (which filters for inference routing).
    Used by name-resolution surfaces that need to answer "is this machine
    known?" — a conversational agent should be able to distinguish "this
    name is not in the registry" from "this machine exists but is offline".

    No caching here — the unfiltered set is small and called rarely.
    """
    _ensure_schema()
    import psycopg2

    try:
        conn = psycopg2.connect(_DB_URL)
        cur = conn.cursor()
        cur.execute("""
            SELECT hostname, display_name, ip, os, cpu, ram_gb,
                   network_type, status, ollama_port, ollama_model,
                   ollama_model_batch, inference_rank,
                   in_use_hours, in_use_until, roles, aliases,
                   ssh_enabled, ssh_user, notes
            FROM machines
            ORDER BY hostname
            """)
        rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        _log.error("[machine_manager] get_all_machines DB_FETCH_FAIL: %s", exc)
        return []

    out = []
    for row in rows:
        (
            hostname,
            display_name,
            ip,
            os_,
            cpu,
            ram_gb,
            network_type,
            status,
            ollama_port,
            ollama_model,
            ollama_model_batch,
            inference_rank,
            in_use_hours_raw,
            in_use_until,
            roles_raw,
            aliases_raw,
            ssh_enabled,
            ssh_user,
            notes,
        ) = row

        def _j(val):
            if isinstance(val, (list, dict)):
                return val
            try:
                return json.loads(val) if val else []
            except Exception:
                return []

        out.append(
            MachineRecord(
                hostname=hostname,
                display_name=display_name or hostname,
                ip=ip,
                os=os_ or "linux",
                cpu=cpu or "",
                ram_gb=ram_gb or 0,
                network_type=network_type or "wifi",
                status=status or "online",
                ollama_port=ollama_port or 11434,
                ollama_model=ollama_model or "llama3.2:1b",
                ollama_model_batch=ollama_model_batch,
                inference_rank=inference_rank,
                in_use_hours=_j(in_use_hours_raw),
                in_use_until=in_use_until,
                roles=_j(roles_raw),
                aliases=_j(aliases_raw),
                ssh_enabled=bool(ssh_enabled) if ssh_enabled is not None else False,
                ssh_user=ssh_user,
                notes=notes,
            )
        )
    return out


def resolve_alias(name: str) -> Optional[str]:
    """Resolve a user-friendly name or alias to canonical hostname."""
    name_lower = name.lower().strip()
    for m in get_ranked_machines():
        if m.hostname.lower() == name_lower:
            return m.hostname
        if name_lower in [a.lower() for a in m.aliases]:
            return m.hostname
    return None


def is_in_use(hostname: str) -> bool:
    """
    True if the machine should not receive inference now.
    Checks DB override first, then in_use_hours window.
    """
    m = get_machine(hostname)
    if m is None:
        return False

    now_utc = datetime.now(timezone.utc)
    now_hour = datetime.now().hour

    # 1. DB override
    if m.in_use_until:
        if m.in_use_until == "indefinite":
            return True
        try:
            until_dt = datetime.fromisoformat(m.in_use_until)
            if until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
            if now_utc < until_dt:
                return True
            # Expired — clear it
            _write_override(hostname, None)
            _log.info("[machine_manager] override expired for %s — cleared", hostname)
        except (ValueError, TypeError):
            return True

    # 2. in_use_hours window
    for start, end in m.in_use_hours:
        if start <= now_hour < end:
            return True

    return False


def set_machine_override(hostname: str, ttl_hours: float = 0) -> str:
    """
    Mark machine as in-use — exclude from inference routing.
    ttl_hours=0 = indefinite until cleared.
    Returns status string.
    """
    canonical = resolve_alias(hostname)
    if not canonical:
        known = [m.hostname for m in get_ranked_machines()]
        return f"ERROR: '{hostname}' not found. Known: {known}"

    if ttl_hours > 0:
        until = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()
    else:
        until = "indefinite"

    _write_override(canonical, until)
    ttl_str = f" for {ttl_hours}h" if ttl_hours > 0 else " (until cleared)"
    _log.info("MACHINE_IN_USE|set|host=%s|ttl=%s", canonical, ttl_hours or "indefinite")
    export_to_machines_json()  # refresh file echo
    return f"{canonical} marked in-use{ttl_str}."


def clear_machine_override(hostname: str) -> str:
    """Return machine to inference routing. Returns status string."""
    canonical = resolve_alias(hostname)
    if not canonical:
        known = [m.hostname for m in get_ranked_machines()]
        return f"ERROR: '{hostname}' not found. Known: {known}"

    m = get_machine(canonical)
    if m and m.in_use_until:
        _write_override(canonical, None)
        _log.info("MACHINE_IN_USE|clear|host=%s", canonical)
        export_to_machines_json()  # refresh file echo
        return f"{canonical} cleared — available for inference."
    return f"{canonical} had no override (already available)."


def get_availability_report() -> str:
    """Human-readable availability status of all inference machines."""
    machines = get_ranked_machines()
    if not machines:
        return "No machines in DB."
    lines = []
    for m in machines:
        in_use = is_in_use(m.hostname)
        override_note = ""
        if m.in_use_until:
            override_note = f" [override: {m.in_use_until[:16] if m.in_use_until != 'indefinite' else 'indefinite'}]"
        elif in_use:
            hour = datetime.now().hour
            matching = [f"{s}-{e}" for s, e in m.in_use_hours if s <= hour < e]
            override_note = f" [hours window: {', '.join(matching)}]"
        state = "IN USE" if in_use else "available"
        lines.append(
            f"  rank={m.inference_rank} {m.hostname:20s} {state:10s} "
            f"{m.network_type:5s} {m.ram_gb}GB{override_note}"
        )
    return "Machine availability:\n" + "\n".join(lines)


# ── T-machines-json-phaseout: echo DB → file ──────────────────────────────────
# The machines.json file is an ECHO of the DB, not the source of truth. Akien
# reads it for at-a-glance cluster state (2026-04-14). Any writer that used to
# modify the file directly should now update the DB (machine_manager) and then
# call export_to_machines_json() to refresh the file.
#
# Existing readers (push_sources MachinesWatcher, boot_check, cluster_ssh,
# machine_lookup) can keep reading the file — they now see a fresh DB-echo
# every time a write happens through machine_manager.


def export_to_machines_json(path: Optional["Path"] = None) -> bool:  # noqa: F821
    """Write an echo of the current machines table to machines.json.

    Called after any write path that mutates machine records, so the file
    stays fresh as a human-readable snapshot of the DB. Returns True on
    success, False on any error (best-effort, never crashes the caller).

    path defaults to paths().machines_json if None.
    """
    import json as _json
    from pathlib import Path as _Path

    from ..paths import paths as _paths

    target = path or _paths().machines_json
    try:
        target = _Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)

        machines = get_all_machines()
        payload = {
            "_meta": {
                "description": (
                    "Cluster machine registry. ECHO of the DB machines table — "
                    "do not edit by hand. Writes go through machine_manager."
                ),
                "source_of_truth": "public.machines table in Postgres home DB",
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "exported_by": "machine_manager.export_to_machines_json",
                "schema_version": 5,
            },
            "machines": [
                {
                    "hostname": m.hostname,
                    "display_name": m.display_name,
                    "ip": m.ip,
                    "os": m.os,
                    "cpu": m.cpu,
                    "ram_gb": m.ram_gb,
                    "network_type": m.network_type,
                    "status": m.status,
                    "ollama_port": m.ollama_port,
                    "ollama_model": m.ollama_model,
                    "ollama_model_batch": m.ollama_model_batch,
                    "inference_rank": m.inference_rank,
                    "in_use_hours": m.in_use_hours,
                    "in_use_until": m.in_use_until,
                    "roles": m.roles,
                    "aliases": m.aliases,
                    "ssh": m.ssh_enabled,
                    "ssh_user": m.ssh_user,
                    "notes": m.notes,
                }
                for m in machines
            ],
        }
        target.write_text(_json.dumps(payload, indent=2), encoding="utf-8")
        _log.info(
            "machines.json echo refreshed: %d machines → %s", len(machines), target
        )
        return True
    except Exception as exc:
        _log.warning("machines.json echo refresh failed: %s", exc)
        return False


def register_self(hostname: str, ip: str) -> bool:
    """First-boot self-registration. Idempotent — no-op if already registered.

    Replaces main.py's direct writes to machines.json. This adds the row via
    the DB and then refreshes the file echo. Returns True if a new row was
    inserted, False if the machine was already present or on error.
    """
    import psycopg2

    try:
        _ensure_schema()
        conn = psycopg2.connect(_DB_URL)
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM machines WHERE hostname = %s", (hostname,))
        if cur.fetchone():
            conn.close()
            _log.info("register_self: %s already in machines table", hostname)
            return False
        try:
            from ..network.system_proxy import system_proxy as _sp

            _hw = _sp.hardware
        except Exception:
            _hw = {}
        cur.execute(
            """
            INSERT INTO machines
              (hostname, display_name, ip, os, cpu, ram_gb, network_type,
               status, ollama_port, ollama_model, inference_rank,
               in_use_hours, roles, aliases, ssh_enabled, notes)
            VALUES (%s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s, %s)
            """,
            (
                hostname,
                hostname,
                ip if ip != "unknown" else _hw.get("ip"),
                _hw.get("os", "unknown"),
                _hw.get("cpu", "unknown"),
                _hw.get("ram_gb", 0),
                _hw.get("network_type", "unknown"),
                "online",
                11434,
                _hw.get("ollama_model", "unknown"),
                None,
                json.dumps([]),
                json.dumps([]),
                json.dumps([]),
                False,
                f"Auto-registered at first boot {datetime.now(timezone.utc).isoformat()}",
            ),
        )
        conn.close()
        _invalidate_cache()
        export_to_machines_json()
        _log.info("register_self: inserted %s into machines table", hostname)
        return True
    except Exception as exc:
        _log.warning("register_self failed for %s: %s", hostname, exc)
        return False
