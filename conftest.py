"""Root conftest — makes lab.utility_closet importable during test runs.

The UU lab/ directory is a namespace package (no __init__.py). Adding
TheIgors to sys.path lets Python merge both lab/ trees so tests can
import both lab.claudecode.* (from UU) and lab.utility_closet.* (from
TheIgors) without changes to source imports.

This is a migration bridge. As utility_closet modules are moved into
devices/, remove their entries from this bridge.
"""

import sys
from pathlib import Path

_THEIGORS = Path.home() / "TheIgors"
if _THEIGORS.exists() and str(_THEIGORS) not in sys.path:
    sys.path.insert(0, str(_THEIGORS))
