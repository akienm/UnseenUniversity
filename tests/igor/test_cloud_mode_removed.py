"""
Grep-gate: cloud_mode concept fully deleted from devices/igor.

RED state: cloud_mode.py still exists + live references in source.
GREEN state: file gone + zero references to the specific dead symbols.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

IGOR_ROOT = Path(__file__).parents[2] / "unseen_university" / "devices" / "igor"

# Dead symbols — specific enough to avoid false-positive "cloud_models" etc.
DEAD_SYMBOLS = [
    "is_cloud_training_active",
    "is_cloud_ok_override",
    "set_cloud_ok_override",
    "clear_cloud_ok_override",
]

# Dead import patterns
DEAD_IMPORT_PATTERNS = [
    r"from\s+[.\w]*cloud_mode\s+import",
    r"import\s+[.\w]*cognition\.cloud_mode",
    r"from\s+\.cognition\.cloud_mode",
    r"from\s+\.\.cognition\.cloud_mode",
]


def _py_files():
    return list(IGOR_ROOT.rglob("*.py"))


def test_cloud_mode_file_deleted():
    """cognition/cloud_mode.py must not exist."""
    cm = IGOR_ROOT / "cognition" / "cloud_mode.py"
    assert not cm.exists(), f"cloud_mode.py still exists at {cm}"


def test_dead_symbols_absent():
    """No .py file in igor should reference any of the dead symbols."""
    found = []
    for py in _py_files():
        src = py.read_text(encoding="utf-8", errors="replace")
        for sym in DEAD_SYMBOLS:
            if sym in src:
                found.append(f"{py.relative_to(IGOR_ROOT)}: {sym!r}")
    assert not found, "Dead symbols still present:\n" + "\n".join(found)


def test_dead_import_patterns_absent():
    """No .py file in igor should import from cloud_mode."""
    found = []
    pattern = re.compile("|".join(DEAD_IMPORT_PATTERNS))
    for py in _py_files():
        src = py.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(src.splitlines(), 1):
            if pattern.search(line):
                found.append(f"{py.relative_to(IGOR_ROOT)}:{i}: {line.strip()}")
    assert not found, "Dead import patterns still present:\n" + "\n".join(found)


def test_igor_main_importable():
    """import unseen_university.devices.igor.main must succeed."""
    import importlib

    mod = importlib.import_module("unseen_university.devices.igor.main")
    assert mod is not None
