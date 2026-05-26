"""
routing_tools.py — Machine availability tools for Igor (#342).

Delegates to machine_manager (DB-backed). Replaces flat machine_overrides.json.

Tools exposed:
  set_machine_in_use(machine, ttl_hours)  — mark machine as in-use
  clear_machine_in_use(machine)           — return machine to routing
  get_machine_availability()              — show current status
"""

import logging

logger = logging.getLogger(__name__)


# ── Thin wrappers around machine_manager ──────────────────────────────────────


def in_use_now(hostname: str) -> bool:
    """True if machine should not receive inference right now."""
    try:
        from lab.utility_closet.machine_manager import is_in_use

        return is_in_use(hostname)
    except Exception as _e:
        logger.warning("[routing_tools.in_use_now] machine_manager failed: %s", _e)
        return False


def set_machine_in_use(machine: str, ttl_hours: float = 0) -> str:
    """
    Mark a machine as in-use — remove it from inference routing.
    machine: hostname or alias (e.g. 'yoga9i', 'the dell', 'akiendell')
    ttl_hours: 0 = indefinite until cleared; >0 = expires after N hours
    """
    from lab.utility_closet.machine_manager import set_machine_override

    return set_machine_override(machine, ttl_hours)


def clear_machine_in_use(machine: str) -> str:
    """
    Mark a machine as available — return it to inference routing.
    machine: hostname or alias (e.g. 'yoga9i', 'the dell', 'akiendell')
    """
    from lab.utility_closet.machine_manager import clear_machine_override

    return clear_machine_override(machine)


def get_machine_availability() -> str:
    """Show current availability status of all inference machines."""
    from lab.utility_closet.machine_manager import get_availability_report

    return get_availability_report()


# ── Tool registration ──────────────────────────────────────────────────────────

from devices.igor.tools.registry import Tool, registry  # noqa: E402

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
            "Use when you leave a machine ('leaving my desk', 'heading to the living room'). "
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
