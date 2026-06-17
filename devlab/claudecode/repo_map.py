#!/usr/bin/env python3
"""
repo_map.py — Compact symbol map of Python files for sprint orientation.

Generates a human-readable symbol index (module doc, classes, methods,
top-level functions) from a set of files or directories.  Intended as a
cheap orientation pass at sprint-planning time: one Bash call instead of
3–5 Read/Grep calls to understand what's in affected files.

No new dependencies — uses stdlib ast only.

Usage:
    python3 repo_map.py <file_or_dir> [<file_or_dir> ...]
    python3 repo_map.py --json <file_or_dir> [...]
    python3 repo_map.py --root /path/to/repo <file_or_dir> [...]
"""

from __future__ import annotations

import argparse
import ast
import json as json_mod
import sys
from pathlib import Path
from typing import Generator

# Maximum methods shown per class before "… N more" truncation.
_MAX_METHODS = 8


def _fmt_sig(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Format a function/method node as 'name(args)'."""
    parts: list[str] = []
    a = node.args
    parts.extend(arg.arg for arg in a.args)
    if a.vararg:
        parts.append(f"*{a.vararg.arg}")
    if a.kwarg:
        parts.append(f"**{a.kwarg.arg}")
    return f"{node.name}({', '.join(parts)})"


def parse_file(path: Path) -> dict:
    """Extract symbol map from a single Python file.

    Returns a dict with keys: path, doc, classes, functions.
    On parse failure returns path + error keys only.
    """
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return {
            "path": str(path),
            "error": f"SyntaxError: {exc.msg} (line {exc.lineno})",
        }

    module_doc = ast.get_docstring(tree)

    classes: list[dict] = []
    functions: list[str] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            class_doc = ast.get_docstring(node)
            methods = [
                _fmt_sig(child)
                for child in ast.iter_child_nodes(node)
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            classes.append(
                {
                    "name": node.name,
                    "doc": class_doc.split("\n")[0] if class_doc else None,
                    "methods": methods,
                }
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(_fmt_sig(node))

    return {
        "path": str(path),
        "doc": module_doc.split("\n")[0] if module_doc else None,
        "classes": classes,
        "functions": functions,
    }


def collect_files(paths: list[Path]) -> Generator[Path, None, None]:
    """Yield .py files from a mix of file and directory paths.

    Skips __pycache__, hidden directories, and non-Python files.
    """
    for p in paths:
        if p.is_file():
            if p.suffix == ".py":
                yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*.py")):
                if any(
                    part.startswith(".") or part == "__pycache__"
                    for part in f.relative_to(p).parts
                ):
                    continue
                yield f


def _relpath(path_str: str, root: Path) -> str:
    try:
        return str(Path(path_str).relative_to(root))
    except ValueError:
        return path_str


def render_text(entries: list[dict], root: Path | None = None) -> str:
    """Render a list of parse_file() results as a compact human-readable map."""
    effective_root = root or Path.cwd()
    lines: list[str] = []

    for entry in entries:
        path_label = _relpath(entry["path"], effective_root)

        if "error" in entry:
            lines.append(f"{path_label}  [{entry['error']}]")
            continue

        header = path_label
        if entry.get("doc"):
            header += f"  # {entry['doc'][:80]}"
        lines.append(header)

        for cls in entry.get("classes", []):
            cls_line = f"  class {cls['name']}"
            if cls.get("doc"):
                cls_line += f"  # {cls['doc'][:60]}"
            lines.append(cls_line)
            methods = cls["methods"]
            for m in methods[:_MAX_METHODS]:
                lines.append(f"    - {m}")
            if len(methods) > _MAX_METHODS:
                lines.append(f"    ... ({len(methods) - _MAX_METHODS} more)")

        for fn in entry.get("functions", []):
            lines.append(f"  def {fn}")

    return "\n".join(lines)


def build_map(paths: list[Path]) -> list[dict]:
    """Public entry point: collect files from paths and parse each."""
    return [parse_file(f) for f in collect_files(paths)]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compact symbol map of Python files for sprint orientation."
    )
    parser.add_argument("paths", nargs="+", type=Path, metavar="file_or_dir")
    parser.add_argument("--json", action="store_true", help="Output structured JSON")
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="Root directory for relative path display (default: cwd)",
    )
    args = parser.parse_args()

    entries = build_map(args.paths)

    if not entries:
        print("# repo_map: no Python files found", file=sys.stderr)
        return 0

    if args.json:
        print(json_mod.dumps(entries, indent=2))
    else:
        print(render_text(entries, root=args.root))

    return 0


if __name__ == "__main__":
    sys.exit(main())
