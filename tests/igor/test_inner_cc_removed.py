"""
Grep-gate: assert inner_cc is fully removed from unseen_university/devices/igor/.

RED stub — must FAIL before the removal commit (inner_cc.py still exists).
Goes GREEN after T-igor-inner-cc-assess removal commit.
"""

import importlib
import subprocess
import sys
from pathlib import Path


IGOR_DIR = Path(__file__).parent.parent.parent / "unseen_university" / "devices" / "igor"


def _grep_inner_cc_refs() -> list[str]:
    """Return list of lines containing 'inner_cc' in igor cognition/tools/memory."""
    hits = []
    for sub in ("cognition", "tools", "memory"):
        subdir = IGOR_DIR / sub
        if not subdir.exists():
            continue
        for py in subdir.rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            text = py.read_text(errors="replace")
            for i, line in enumerate(text.splitlines(), 1):
                if "inner_cc" in line:
                    hits.append(f"{py.relative_to(IGOR_DIR)}:{i}: {line.strip()}")
    return hits


def test_no_inner_cc_refs_in_igor():
    """Zero references to 'inner_cc' in igor cognition/, tools/, memory/."""
    hits = _grep_inner_cc_refs()
    assert hits == [], (
        f"Found {len(hits)} inner_cc reference(s) — must be 0:\n"
        + "\n".join(hits)
    )


def test_inner_cc_tool_file_deleted():
    """tools/inner_cc.py must not exist."""
    target = IGOR_DIR / "tools" / "inner_cc.py"
    assert not target.exists(), f"File still present: {target}"


def test_training_pass_file_deleted():
    """tools/training_pass.py must not exist (was inner_cc-only)."""
    target = IGOR_DIR / "tools" / "training_pass.py"
    assert not target.exists(), f"File still present: {target}"


def test_igor_main_imports_cleanly():
    """import unseen_university.devices.igor.main must succeed with no ImportError."""
    result = subprocess.run(
        [sys.executable, "-c", "import unseen_university.devices.igor.main"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"igor.main import failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
