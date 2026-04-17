"""
machine_manager.py — Re-export shim.

Implementation moved to lab/utility_closet/machine_manager.py as part of the
utility closet rack architecture (T-uc-machine-manager-shelf). This shim
re-exports all public names so existing imports continue to work.

All new code should import from lab.utility_closet.machine_manager directly.
"""

# Re-export everything from the canonical location
from lab.utility_closet.machine_manager import (  # noqa: F401
    MachineRecord,
    clear_machine_override,
    get_all_machines,
    get_availability_report,
    get_machine,
    get_ranked_machines,
    is_in_use,
    register_self,
    resolve_alias,
    set_machine_override,
)
