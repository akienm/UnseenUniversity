"""pytest configuration — path setup and shared fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

# lab.claudecode lives in TheIgors, not this repo.
# Add it before collection so test_cc_task_listener.py can import it.
_theigors = Path.home() / "TheIgors"
if _theigors.exists() and str(_theigors) not in sys.path:
    sys.path.insert(0, str(_theigors))
