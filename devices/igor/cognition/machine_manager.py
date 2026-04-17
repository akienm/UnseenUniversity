"""
machine_manager.py — Re-export shim.

Implementation moved to lab/utility_closet/machine_manager.py as part of the
utility closet rack architecture (T-uc-machine-manager-shelf). This shim
re-exports all names so existing imports and patches continue to work.

All new code should import from lab.utility_closet.machine_manager directly.
"""

# Re-export everything from the canonical location (including private names
# that tests may patch via unittest.mock)
from lab.utility_closet.machine_manager import *  # noqa: F401, F403
from lab.utility_closet.machine_manager import _write_override  # noqa: F401
