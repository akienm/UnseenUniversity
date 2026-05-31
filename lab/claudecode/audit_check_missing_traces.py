#!/usr/bin/env python3
"""
audit_check_missing_traces.py — trace emission detector.

Walks devices/*/device.py and flags BaseDevice subclasses that never call
trace_record() anywhere in the file. Every rack device should emit structured
JSONL trace events so /diagnose works uniformly.

Empty stdout = pass. Non-empty = list of violations, one per line.

Registered as a forever audit check (AR-008):
  python3 lab/claudecode/audit_add.py add forever
  basedevice-must-emit-traces --kind shell
  --pattern 'python3 lab/claudecode/audit_check_missing_traces.py'
  --severity med
"""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVICES_ROOT = REPO_ROOT / "devices"


def _has_basedevice_class(tree: ast.AST) -> bool:
    """Return True if any class in this file directly inherits BaseDevice."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for base in node.bases:
            if isinstance(base, ast.Name) and base.id == "BaseDevice":
                return True
            if isinstance(base, ast.Attribute) and base.attr == "BaseDevice":
                return True
    return False


def _has_trace_record_call(tree: ast.AST) -> bool:
    """Return True if trace_record() is called anywhere in this file."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "trace_record":
            return True
        if isinstance(func, ast.Name) and func.id == "trace_record":
            return True
    return False


def _check_file(path: Path) -> str | None:
    """Return a violation line if the file has a BaseDevice class but no trace_record call."""
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return None

    if not _has_basedevice_class(tree):
        return None

    if _has_trace_record_call(tree):
        return None

    rel = path.relative_to(REPO_ROOT)
    return str(rel)


def main() -> int:
    if not DEVICES_ROOT.exists():
        print(f"devices root not found: {DEVICES_ROOT}", file=sys.stderr)
        return 2

    violations: list[str] = []
    for dev_py in sorted(DEVICES_ROOT.glob("*/device.py")):
        if "__pycache__" in str(dev_py):
            continue
        v = _check_file(dev_py)
        if v:
            violations.append(v)

    for v in violations:
        print(v)
    return 0 if not violations else 1


if __name__ == "__main__":
    sys.exit(main())
