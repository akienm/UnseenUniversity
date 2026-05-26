"""
machine_lookup.py — T-machine-names-registry.

Tool surface for resolving machine names through the canonical registry
(cognition/machine_manager.py) instead of pattern-matching them as
hostnames. Igor drafted this ticket on 2026-04-12 after observing that
CC abbreviated 'akienyoga9i' to 'yoga9i' and then treated 'yoga9i' as
if it were a hostname, leading to a "machine not up" loop.

The registry already exists (machines.json with explicit aliases per
machine; resolve_alias() function in machine_manager.py). What was
missing is a tool surface that conversational agents can call to
resolve names BEFORE acting on them.

Tools registered:

  - machine_lookup(name): resolve a name (canonical hostname or any
    alias) to a full machine record. Returns dict with hostname, ip,
    role, aliases, status, etc. If the name is not in the registry,
    returns a clear error string — never silently pattern-matches.

  - machine_list_all(): list every machine in the registry with
    its aliases, IP, and key fields. The "what machines exist?"
    discovery surface so callers can browse before referencing.

These are LOW-inertia tools/ additions that compose with the existing
machine_manager.py without modifying it.
"""

from typing import Optional

from devices.igor.tools.registry import Tool, registry


def _format_machine(m) -> str:
    """Render a single MachineRecord as a human-readable block.

    Field set matches the actual MachineRecord dataclass in
    cognition/machine_manager.py — uses getattr fallbacks where the
    JSON might carry extra fields that the dataclass doesn't model.
    """
    aliases = ", ".join(m.aliases) if m.aliases else "(none)"
    roles = ", ".join(m.roles) if m.roles else "(none)"
    return (
        f"hostname: {m.hostname}\n"
        f"  display_name: {getattr(m, 'display_name', '') or '(unset)'}\n"
        f"  ip: {m.ip or '(none)'}\n"
        f"  os: {m.os or '(unknown)'}\n"
        f"  status: {m.status or '(unknown)'}\n"
        f"  network_type: {getattr(m, 'network_type', '') or '(unknown)'}\n"
        f"  inference_rank: {m.inference_rank if m.inference_rank is not None else '(none)'}\n"
        f"  ollama: {getattr(m, 'ollama_host', '(unknown)')} "
        f"model={getattr(m, 'ollama_model', '(none)')}\n"
        f"  roles: {roles}\n"
        f"  aliases: {aliases}"
    )


def _lookup_in_all_machines(name: str):
    """Find a MachineRecord by canonical hostname or any alias, including
    offline and unranked machines. Returns the record or None.

    Uses get_all_machines (not get_ranked_machines) so a conversational
    agent asking 'is pi a known machine?' gets a useful answer even
    when the pi is offline or has no inference_rank.
    """
    from lab.utility_closet.machine_manager import get_all_machines

    needle = name.lower().strip()
    for m in get_all_machines():
        if m.hostname.lower() == needle:
            return m
        if needle in [a.lower() for a in (m.aliases or [])]:
            return m
    return None


def machine_lookup(name: str, **_) -> str:
    """Resolve a machine name (canonical hostname or any registered alias)
    to its full record. Never pattern-matches — unregistered names return
    a clear error so callers can ask for clarification instead of guessing.

    Use this BEFORE acting on any conversational mention of a machine.
    """
    if not name or not str(name).strip():
        return "[ERROR] machine_lookup requires a name."

    try:
        rec = _lookup_in_all_machines(name)
    except Exception as e:
        return f"[ERROR] machine_lookup({name!r}) failed: {e}"

    if rec is None:
        return (
            f"[NOT REGISTERED] {name!r} is not in the canonical machine registry. "
            f"Use machine_list_all() to see registered machines and their aliases. "
            f"Do not treat unregistered names as hostnames."
        )

    return _format_machine(rec)


def machine_list_all(**_) -> str:
    """List every machine in the canonical registry with its aliases.

    Discovery surface — answers "what machines exist?" before any
    conversational reference. Includes offline and unranked machines so
    the caller can see the full picture, not just the inference-routable
    subset. Sorted by canonical hostname.
    """
    try:
        from lab.utility_closet.machine_manager import get_all_machines
    except Exception as e:
        return f"[ERROR] machine_manager import failed: {e}"

    try:
        machines = get_all_machines()
    except Exception as e:
        return f"[ERROR] get_all_machines failed: {e}"

    if not machines:
        return "(machine registry is empty)"

    sorted_machines = sorted(machines, key=lambda m: m.hostname)
    blocks = []
    for m in sorted_machines:
        blocks.append(_format_machine(m))
    return "Canonical machine registry:\n\n" + "\n\n".join(blocks)


# ── Tool registrations ───────────────────────────────────────────────────────


registry.register(
    Tool(
        name="machine_lookup",
        description=(
            "Resolve a machine name (canonical hostname or any registered "
            "alias like 'yoga9i', 'the dell', 'pi') to its full record. "
            "Returns hostname, IP, OS, status, capabilities, roles, aliases. "
            "Returns a [NOT REGISTERED] error for unknown names — does NOT "
            "pattern-match or guess. ALWAYS use this before treating a "
            "conversational mention as a hostname."
        ),
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Machine name or alias to resolve",
                },
            },
            "required": ["name"],
        },
        fn=machine_lookup,
    )
)

registry.register(
    Tool(
        name="machine_list_all",
        description=(
            "List every machine in the canonical registry with its aliases, "
            "IP, OS, status, capabilities, and roles. Use this to discover "
            "what machines exist and what aliases are registered for each."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=machine_list_all,
    )
)
