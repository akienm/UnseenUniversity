"""
routing_tools.py — D211: Machine availability signal tools.

Igor (or Akien via habit) can mark a machine in-use or available.
Overrides are persisted in ~/.TheIgors/local/machine_overrides.json.

in_use_now(hostname) checks:
  1. machine_overrides.json explicit override (with optional TTL)
  2. machines.json in_use_hours [[start, end]] against current local hour
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ..paths import paths

logger = logging.getLogger(__name__)

_MACHINES_JSON = paths().runtime / "local" / "machines.json"
_OVERRIDES_JSON = paths().runtime / "local" / "machine_overrides.json"


# ── shared helpers ─────────────────────────────────────────────────────────────


def _load_machines() -> list[dict]:
    try:
        return json.loads(_MACHINES_JSON.read_text()).get("machines", [])
    except Exception:
        return []


def _resolve_alias(name: str) -> str | None:
    """Resolve a hostname or alias to canonical hostname. Returns None if not found."""
    name_lower = name.lower().strip()
    for m in _load_machines():
        if m["hostname"].lower() == name_lower:
            return m["hostname"]
        for alias in m.get("aliases", []):
            if alias.lower() == name_lower:
                return m["hostname"]
    return None


def _load_overrides() -> dict:
    try:
        if _OVERRIDES_JSON.exists():
            return json.loads(_OVERRIDES_JSON.read_text())
    except Exception:
        pass
    return {}


def _save_overrides(data: dict) -> None:
    _OVERRIDES_JSON.parent.mkdir(parents=True, exist_ok=True)
    _OVERRIDES_JSON.write_text(json.dumps(data, indent=2))


def in_use_now(hostname: str) -> bool:
    """
    True if the machine should not be used for inference right now.
    Checks explicit overrides first, then in_use_hours windows.
    """
    now_utc = datetime.now(timezone.utc)
    now_local_hour = datetime.now().hour

    # 1. Explicit override
    overrides = _load_overrides()
    if hostname in overrides:
        entry = overrides[hostname]
        until = entry.get("until")
        if until is None:
            return True  # indefinite
        try:
            until_dt = datetime.fromisoformat(until)
            if until_dt.tzinfo is None:
                until_dt = until_dt.replace(tzinfo=timezone.utc)
            if now_utc < until_dt:
                return True
            # Expired — clean it up silently
            del overrides[hostname]
            _save_overrides(overrides)
        except (ValueError, TypeError):
            return True

    # 2. in_use_hours windows from machines.json
    for m in _load_machines():
        if m["hostname"] == hostname:
            for start, end in m.get("in_use_hours", []):
                if start <= now_local_hour < end:
                    return True
            break

    return False


# ── Tools ─────────────────────────────────────────────────────────────────────


def set_machine_in_use(machine: str, ttl_hours: float = 0) -> str:
    """
    Mark a machine as in-use — remove it from inference routing.
    machine: hostname or alias (e.g. 'yoga9i', 'the dell', 'akiendell')
    ttl_hours: 0 = indefinite until cleared; >0 = expires after N hours
    """
    hostname = _resolve_alias(machine)
    if not hostname:
        return f"ERROR: '{machine}' not found in machines list. Known: {[m['hostname'] for m in _load_machines()]}"

    overrides = _load_overrides()
    until = None
    if ttl_hours > 0:
        until = (datetime.now(timezone.utc) + timedelta(hours=ttl_hours)).isoformat()

    overrides[hostname] = {"in_use": True, "until": until}
    _save_overrides(overrides)

    ttl_str = f" for {ttl_hours}h" if ttl_hours > 0 else " (until cleared)"
    logger.info(
        "MACHINE_IN_USE|set|host=%s|ttl=%s", hostname, ttl_hours or "indefinite"
    )
    return f"{hostname} marked in-use{ttl_str} — excluded from inference routing."


def clear_machine_in_use(machine: str) -> str:
    """
    Mark a machine as available — return it to inference routing.
    machine: hostname or alias (e.g. 'yoga9i', 'the dell', 'akiendell')
    """
    hostname = _resolve_alias(machine)
    if not hostname:
        return f"ERROR: '{machine}' not found. Known: {[m['hostname'] for m in _load_machines()]}"

    overrides = _load_overrides()
    if hostname in overrides:
        del overrides[hostname]
        _save_overrides(overrides)
        logger.info("MACHINE_IN_USE|clear|host=%s", hostname)
        return f"{hostname} cleared — available for inference routing."
    return f"{hostname} had no override set (already available)."


def get_machine_availability() -> str:
    """Show current availability status of all inference machines."""
    machines = _load_machines()
    if not machines:
        return "No machines found in machines.json."

    lines = []
    for m in machines:
        hostname = m["hostname"]
        rank = m.get("inference_rank", "-")
        if rank == "-":
            continue  # offline/no-rank machines
        status = m.get("status", "unknown")
        if status != "online":
            lines.append(f"  rank={rank} {hostname:20s} OFFLINE")
            continue
        in_use = in_use_now(hostname)
        overrides = _load_overrides()
        override_note = ""
        if hostname in overrides:
            until = overrides[hostname].get("until")
            override_note = f" [manual override{', until ' + until[:16] if until else ', indefinite'}]"
        elif in_use:
            override_note = f" [in_use_hours window]"
        state = "IN USE" if in_use else "available"
        net = m.get("network_type", "?")
        ram = m.get("ram_gb", "?")
        roles = ",".join(m.get("roles", [])) or "-"
        lines.append(
            f"  rank={rank} {hostname:20s} {state:10s} {net:5s} {ram}GB  roles={roles}{override_note}"
        )

    return "Machine availability:\n" + "\n".join(lines)


# ── Tool registration ──────────────────────────────────────────────────────────

from .registry import Tool, registry  # noqa: E402

registry.register(
    Tool(
        name="set_machine_in_use",
        description=(
            "Mark a machine as in-use — exclude it from inference routing. "
            "Use when you sit down at a machine ('I'm at akiendell', 'I'm on the yoga'). "
            "machine = hostname or alias. ttl_hours = 0 means indefinite until cleared."
        ),
        parameters={
            "machine": "string — hostname or alias (e.g. 'yoga9i', 'the dell', 'akiendell')",
            "ttl_hours": "number — hours until auto-clear; 0 = indefinite (default 0)",
        },
        fn=set_machine_in_use,
    )
)

registry.register(
    Tool(
        name="clear_machine_in_use",
        description=(
            "Mark a machine as available — return it to inference routing. "
            "Use when you leave a machine ('I'm moving away from akiendell', 'done with the yoga'). "
            "machine = hostname or alias."
        ),
        parameters={
            "machine": "string — hostname or alias (e.g. 'yoga9i', 'the dell', 'akiendell')",
        },
        fn=clear_machine_in_use,
    )
)

registry.register(
    Tool(
        name="get_machine_availability",
        description="Show current availability of all inference machines — which are in use vs available.",
        parameters={},
        fn=get_machine_availability,
    )
)
