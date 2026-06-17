#!/usr/bin/env python3
"""
audit_check_interface_logging.py — interface-crossing log enforcement.

Walks devices/*/device.py and flags methods in BaseDevice subclasses that
contain interface-crossing calls without a corresponding log call in the
same method body.

Interface-crossing primitives detected:
  - subprocess.Popen / subprocess.run / subprocess.call / subprocess.check_output
  - post_to_channel() (bare call — the channel library function)

The rule: every method that directly invokes one of these primitives must
also contain a log call (self._log.*/log.*./logging.*) so the crossing is
traceable by /diagnose. Logging only in a wrapper does not count — the
primitive-calling method must log.

Exit 0: no violations.
Exit 1: violations found (printed as "file:class:method (crossing_type)").
Exit 2: tool error (devices root missing, etc.)

Registered as AR-009 in audit_checks.json.
"""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVICES_ROOT = REPO_ROOT / "devices"

_SUBPROCESS_NAMES = frozenset({"Popen", "run", "call", "check_output", "check_call"})
_CHANNEL_POST_NAMES = frozenset({"post_to_channel"})
_LOG_ATTRS = frozenset(
    {"info", "warning", "warn", "error", "debug", "critical", "exception"}
)


def _crossing_type(call_node: ast.Call) -> str | None:
    """Return the crossing type label if this call is an interface crossing, else None."""
    func = call_node.func
    if isinstance(func, ast.Attribute):
        if (
            isinstance(func.value, ast.Name)
            and func.value.id == "subprocess"
            and func.attr in _SUBPROCESS_NAMES
        ):
            return f"subprocess.{func.attr}"
    if isinstance(func, ast.Name) and func.id in _CHANNEL_POST_NAMES:
        return "post_to_channel"
    return None


def _has_log_call(method_node: ast.FunctionDef) -> bool:
    """Return True if the method body contains any log call."""
    for node in ast.walk(method_node):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _LOG_ATTRS:
            return True
    return False


def _check_class(class_node: ast.ClassDef, rel_path: str) -> list[str]:
    """Return violation lines for methods in this class that cross interfaces without logging."""
    violations = []
    for node in ast.iter_child_nodes(class_node):
        if not isinstance(node, ast.FunctionDef):
            continue
        crossings = [
            _crossing_type(c) for c in ast.walk(node) if isinstance(c, ast.Call)
        ]
        crossings = [c for c in crossings if c]
        if not crossings:
            continue
        if not _has_log_call(node):
            crossing_types = ", ".join(sorted(set(crossings)))
            violations.append(
                f"{rel_path}:{class_node.name}:{node.name} ({crossing_types})"
            )
    return violations


def _check_file(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return []

    rel = str(path.relative_to(REPO_ROOT))
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        base_names = set()
        for base in node.bases:
            if isinstance(base, ast.Name):
                base_names.add(base.id)
            elif isinstance(base, ast.Attribute):
                base_names.add(base.attr)
        if "BaseDevice" not in base_names:
            continue
        violations.extend(_check_class(node, rel))
    return violations


def main() -> int:
    if not DEVICES_ROOT.exists():
        print(f"devices root not found: {DEVICES_ROOT}", file=sys.stderr)
        return 2

    violations = []
    for dev_py in sorted(DEVICES_ROOT.glob("*/device.py")):
        if "__pycache__" in str(dev_py):
            continue
        violations.extend(_check_file(dev_py))

    for v in violations:
        print(v)
    return 0 if not violations else 1


if __name__ == "__main__":
    sys.exit(main())
