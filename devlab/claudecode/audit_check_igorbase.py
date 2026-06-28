#!/usr/bin/env python3
"""
audit_check_igorbase.py — D125 enforcement helper.

Walks devices/igor/{cognition,memory,tools,network,brainstem}/ and finds
class definitions whose bases don't include IgorBase AND aren't exclusively
third-party (BaseModel, Enum, ABC, dataclass, Exception, Generic, Protocol,
NamedTuple, TypedDict, object).

Does file-local transitive resolution: if class B inherits from A, and A
inherits from IgorBase in the same file, B is considered compliant.

Empty stdout = pass. Non-empty = list of violations, one per line.

Used as a registered audit check via `audit_add.py add forever
primary-classes-must-inherit-igorbase --kind shell --pattern
'python3 devlab/claudecode/audit_check_igorbase.py'`.
"""

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "unseen_university" / "devices" / "igor"

# Bases that exempt a class from the IgorBase requirement
THIRD_PARTY_BASES = {
    "BaseModel",
    "Enum",
    "IntEnum",
    "StrEnum",
    "NamedTuple",
    "str",
    "int",
    "float",
    "bytes",
    "TypedDict",
    "object",
    "ABC",
    "ABCMeta",
    "Protocol",
    "Generic",
    "Exception",
    "ValueError",
    "TypeError",
    "RuntimeError",
    "Tuple",
    "Dict",
    "List",
    "Set",
    "FrozenSet",
    "dataclass",
    "Thread",
    "Process",
    "Server",
    "BaseHTTPRequestHandler",
    "HTTPServer",
    # Discord.py / third-party client bases
    "Client",
    "Cog",
    # ctypes
    "Structure",
    "Union",
    # Tkinter
    "Frame",
    "Tk",
    # stdlib logging
    "Handler",
    "Logger",
    "Formatter",
}

# Subdirectories entirely excluded from the check (vendored / external code)
EXCLUDED_SUBDIRS = {"ebook_drm"}

# Igor-internal base classes defined in OTHER files that already inherit
# IgorBase / AgentBase transitively. Subclasses in separate files are exempt.
KNOWN_IGORBASE_ANCESTORS = {
    "BasePushSource",  # push_sources.py — IgorBase direct
    "BaseCascadeLevel",  # experiment_cascade.py — IgorBase direct
    "BaseReasoner",  # reasoners/base.py — IgorBase direct
    "BaseInterruptor",  # interruptors.py — IgorBase direct
    "RackModule",  # devices/web_server/ — AgentBase direct
    "Rack",  # devices/web_server/ — AgentBase direct
    "Transport",  # unseen_university/bus/ — AgentBase direct
    "MatterController",  # devices/ — AgentBase direct
}

# Names that the audit explicitly exempts even with no IgorBase ancestry.
# Reserved for: primitives too narrow to benefit from inheritance (__slots__
# hot-path classes), boot utilities that run before IgorBase is importable,
# and self-contained singletons that intentionally use AgentBase to avoid
# pulling in the loguru/DiagnosticBase chain (registry.py design contract).
EXEMPT_CLASS_NAMES = {
    "AgentBase",  # IS the base — definitionally exempt
    "TimerHandle",  # lightweight slotted timer in logging_setup.py
    "PathManager",  # paths.py — boot-time utility, runs before IgorBase is safe
    "ToolRegistry",  # registry.py is intentionally self-contained (AgentBase only); uses no IgorBase features
}

# Only enforce on these subdirectories
PRIMARY_DIRS = ("cognition", "memory", "tools", "network", "brainstem")


def _is_primary(path: Path) -> bool:
    rel = path.relative_to(SOURCE_ROOT)
    parts = rel.parts
    if not parts:
        return False
    if parts[0] not in PRIMARY_DIRS:
        return False
    # Exclude vendored subdirectories
    if any(p in EXCLUDED_SUBDIRS for p in parts):
        return False
    if "__pycache__" in parts:
        return False
    if "tests" in parts or "test" in parts:
        return False
    name = parts[-1]
    if name.startswith("test_") or name.endswith("_test.py"):
        return False
    return True


def _base_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _base_name(node.value)
    return "?"


def _build_local_parents(tree: ast.AST) -> dict[str, list[str]]:
    """Map class name → list of direct base names for all classes in this file."""
    parents: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            parents[node.name] = [_base_name(b) for b in node.bases]
    return parents


def _has_igorbase_transitively(
    class_name: str,
    local_parents: dict[str, list[str]],
    visited: set[str] | None = None,
) -> bool:
    """Return True if class_name has IgorBase anywhere in its local ancestry."""
    if visited is None:
        visited = set()
    if class_name in visited:
        return False
    visited.add(class_name)
    bases = local_parents.get(class_name, [])
    if "IgorBase" in bases:
        return True
    for b in bases:
        if _has_igorbase_transitively(b, local_parents, visited):
            return True
    return False


def _check_file(path: Path) -> list[str]:
    out: list[str] = []
    try:
        tree = ast.parse(path.read_text())
    except (SyntaxError, UnicodeDecodeError):
        return out

    local_parents = _build_local_parents(tree)

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name.startswith("_"):
            continue  # private helpers are not primary component classes
        if node.name in EXEMPT_CLASS_NAMES:
            continue  # explicit primitive exemption
        bases = [_base_name(b) for b in node.bases]

        # Transitive IgorBase check (file-local ancestry)
        if _has_igorbase_transitively(node.name, local_parents):
            continue

        # Any base is a known Igor hierarchy root from another file
        if any(b in KNOWN_IGORBASE_ANCESTORS for b in bases):
            continue

        # All bases are exclusively third-party / stdlib — exempt
        if bases and all(b in THIRD_PARTY_BASES for b in bases):
            continue

        # @dataclass classes are exempted (data containers, not components)
        if any(
            (isinstance(d, ast.Name) and d.id == "dataclass")
            or (
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Name)
                and d.func.id == "dataclass"
            )
            for d in node.decorator_list
        ):
            continue

        rel = path.relative_to(REPO_ROOT)
        bases_str = ", ".join(bases) if bases else ""
        out.append(f"{rel}:{node.lineno} class {node.name}({bases_str})")
    return out


def main() -> int:
    if not SOURCE_ROOT.exists():
        print(f"source root not found: {SOURCE_ROOT}", file=sys.stderr)
        return 2

    violations: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if not _is_primary(path):
            continue
        violations.extend(_check_file(path))

    for v in violations:
        print(v)
    return 0 if not violations else 1


if __name__ == "__main__":
    sys.exit(main())
