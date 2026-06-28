"""callmap.py — reverse callgraph for designated API functions.

WHAT IT IS
──────────
A static analyzer that, for each "API-like" function in the designated
source roots, finds every caller in the repo and emits a markdown index
to docs/callmap.md.

Forward axis (concept → location) is handled by docs-in-code (top-of-file
canonical docstrings). Callmap fills the reverse axis: function → who
calls it. Refactor planning becomes O(1) lookup instead of O(grep).

WHY IT EXISTS
─────────────
2026-05-03: T-cc-queue-write-race needed every queue.json reader migrated
to the canonical Postgres path. Finding them was a grep+eyeball pass
across 25+ files. A pre-built callmap would have made it a 3-second
query. This tool makes the callmap a durable, audit-checked artifact.

API marking convention:
  - Default: all top-level non-_ functions and class methods in
    SOURCE_ROOTS (devlab/claudecode/, unseen_university/devices/igor/tools/) count as API
  - Override: `__api__ = ['name1', 'name2']` module-level list narrows
    the surface explicitly
  - Annotate: `# API: <one-line>` immediately above a function adds a
    description to its callmap entry

Caller detection:
  - Direct calls: `from mod import f; f(...)` and `import mod; mod.f(...)`
    via AST
  - Subprocess: `subprocess.run([..., 'cc_queue.py', ...])` via string-
    matching the script's path. Important because pe_chain calls cc_queue
    via subprocess, not import.

Known blind spots:
  - Dynamic dispatch (`getattr(mod, 'load')()`) — would need runtime
    tracing; out of scope for v1.
  - Inheritance-resolved methods called on a base class — caller's static
    type may not name the right method.

Usage:
  python3 devlab/claudecode/callmap.py             # regenerate docs/callmap.md
  python3 devlab/claudecode/callmap.py --check     # exit 1 if regenerated differs from on-disk

Wire as audit check:
  audit_add.py add forever callmap-fresh \
      --kind shell --pattern 'python3 devlab/claudecode/callmap.py --check' \
      --severity med

Updated 2026-05-03.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]

# Modules whose top-level non-_ functions/methods count as API by default.
SOURCE_ROOTS = (
    REPO_ROOT / "devlab" / "claudecode",
    REPO_ROOT / "unseen_university" / "devices" / "igor" / "tools",
)

# Files we walk for caller detection (entire repo minus excluded dirs).
SCAN_ROOTS = (
    REPO_ROOT / "lab",
    REPO_ROOT / "unseen_university" / "devices" / "igor",
    REPO_ROOT / "tests",
)

EXCLUDE_DIR_NAMES = {"__pycache__", "venv", ".git", "node_modules", "archive"}

# Function names too generic to be useful API entries — every script has a
# `main`, every class has `__init__`, attaching all callers under one
# section would produce noise. Excluded by default; a module's __api__
# list can re-include them explicitly.
NOISE_NAMES = {
    "main",
    "__init__",
    "__main__",
    "__call__",
    "__str__",
    "__repr__",
    "setUp",
    "tearDown",
    "run",
}

OUTPUT_PATH = REPO_ROOT / "docs" / "callmap.md"


# ── Data model ─────────────────────────────────────────────────────────────


@dataclass
class ApiEntry:
    module: str  # dotted module path, e.g. "devlab.claudecode.cc_queue"
    name: str  # function or "Class.method"
    file: Path  # source file
    lineno: int
    description: str = ""  # from `# API: <text>` comment
    callers: list[tuple[Path, int, str]] = field(default_factory=list)
    # caller tuple = (file, lineno, kind) where kind in {"direct","subprocess"}


# ── API discovery ──────────────────────────────────────────────────────────


def _module_path_from_file(path: Path) -> str:
    """Convert /home/.../unseen_university/devices/igor/tools/foo.py → unseen_university.devices.igor.tools.foo.

    Falls back to the file stem when the path isn't under REPO_ROOT (e.g.
    test fixtures in /tmp).
    """
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        # Path outside the repo — best-effort: use the path components
        # below an obvious package boundary, falling back to stem.
        parts_seq = list(path.with_suffix("").parts)
        # Trim leading filesystem prefix; keep last few segments
        trimmed = parts_seq[-4:] if len(parts_seq) >= 4 else parts_seq
        return ".".join(trimmed)
    parts = list(rel.with_suffix("").parts)
    return ".".join(parts)


def _is_excluded(path: Path) -> bool:
    return any(p in EXCLUDE_DIR_NAMES for p in path.parts)


def _safe_rel(path: Path) -> str:
    """relative_to(REPO_ROOT) but falls back to last 4 path components for
    paths outside the repo (e.g. test fixtures in /tmp)."""
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        parts_seq = list(path.parts)
        return str(Path(*parts_seq[-4:])) if len(parts_seq) >= 4 else path.name


def _iter_source_files(roots: Iterable[Path]) -> Iterable[Path]:
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if _is_excluded(path):
                continue
            yield path


def _api_comments_above(source: str, lineno: int) -> str:
    """Pull a `# API: <text>` comment immediately above the function def."""
    lines = source.splitlines()
    # def is at lineno-1 (0-indexed). Walk upward over decorators + comments.
    i = lineno - 2
    while i >= 0:
        stripped = lines[i].strip()
        if stripped.startswith("@"):
            i -= 1
            continue
        if stripped.startswith("# API:"):
            return stripped[len("# API:") :].strip()
        if stripped == "" or stripped.startswith("#"):
            i -= 1
            continue
        break
    return ""


def discover_apis(source_roots: Iterable[Path]) -> list[ApiEntry]:
    """Walk source_roots; emit API entries per the marking convention."""
    apis: list[ApiEntry] = []
    for path in _iter_source_files(source_roots):
        try:
            source = path.read_text()
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue

        # Module-level __api__ override
        explicit: set[str] | None = None
        for node in ast.iter_child_nodes(tree):
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "__api__"
                and isinstance(node.value, (ast.List, ast.Tuple))
            ):
                explicit = set()
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                        explicit.add(elt.value)

        module_dotted = _module_path_from_file(path)

        # Top-level functions
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if explicit is not None:
                    if node.name not in explicit:
                        continue
                else:
                    if node.name.startswith("_") or node.name in NOISE_NAMES:
                        continue
                apis.append(
                    ApiEntry(
                        module=module_dotted,
                        name=node.name,
                        file=path,
                        lineno=node.lineno,
                        description=_api_comments_above(source, node.lineno),
                    )
                )
            elif isinstance(node, ast.ClassDef):
                # Class methods (top-level class only, one level deep)
                for inner in node.body:
                    if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        if explicit is not None:
                            qual = f"{node.name}.{inner.name}"
                            if qual not in explicit and inner.name not in explicit:
                                continue
                        else:
                            if inner.name.startswith("_") or inner.name in NOISE_NAMES:
                                continue
                        apis.append(
                            ApiEntry(
                                module=module_dotted,
                                name=f"{node.name}.{inner.name}",
                                file=path,
                                lineno=inner.lineno,
                                description=_api_comments_above(source, inner.lineno),
                            )
                        )
    return apis


# ── Caller detection ──────────────────────────────────────────────────────


def _bare_name(call: ast.Call) -> str | None:
    """Return the bare function name being called, e.g. 'load' for foo.load()."""
    f = call.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def _resolved_module_for_call(
    call: ast.Call, imports_in_file: dict[str, str]
) -> str | None:
    """Best-effort: resolve which module the call targets via imports.

    imports_in_file: name → module-or-symbol-source. Built from
    `import x as y`, `from x import y`, `import x.y` patterns.
    Only used to disambiguate when name collisions exist between APIs.
    """
    f = call.func
    if isinstance(f, ast.Attribute):
        # mod.func() — walk up to root
        root = f
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id in imports_in_file:
            return imports_in_file[root.id]
        return None
    if isinstance(f, ast.Name):
        # bare func() — was f.id imported via from ... import f ?
        return imports_in_file.get(f.id)
    return None


def _collect_imports(tree: ast.AST) -> dict[str, str]:
    """name → originating-module string. Best-effort for from/import shapes."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            for alias in node.names:
                out[alias.asname or alias.name] = node.module
    return out


def _is_subprocess_call(call: ast.Call) -> bool:
    """True iff this Call node looks like subprocess.run / Popen / os.system / etc."""
    f = call.func
    if isinstance(f, ast.Attribute):
        if isinstance(f.value, ast.Name):
            if f.value.id in ("subprocess", "os") and f.attr in (
                "run",
                "Popen",
                "call",
                "check_output",
                "check_call",
                "system",
                "popen",
            ):
                return True
        elif f.attr in ("run", "Popen") and isinstance(f.value, ast.Attribute):
            if isinstance(f.value.value, ast.Name) and f.value.value.id == "subprocess":
                return True
    elif isinstance(f, ast.Name) and f.id in ("Popen",):
        return True
    return False


def _subprocess_string_constants(call: ast.Call) -> list[str]:
    """Collect every string-constant inside a subprocess-shaped Call's args."""
    out: list[str] = []
    for sub in ast.walk(call):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.append(sub.value)
    return out


def attach_callers(apis: list[ApiEntry], scan_roots: Iterable[Path]) -> None:
    """Walk every .py in scan_roots; for each API, append callers."""
    by_name: dict[str, list[ApiEntry]] = defaultdict(list)
    for api in apis:
        bare = api.name.split(".")[-1]
        by_name[bare].append(api)

    # Map module → script-file string (e.g. cc_queue.py) for subprocess match
    script_filename_for_module: dict[str, str] = {}
    for api in apis:
        script_filename_for_module[api.module] = api.file.name

    seen_subproc_per_module: set[tuple[str, Path, int]] = set()

    # Pre-filter terms: any API bare name OR any script filename. If the
    # file's raw text contains none of these as substrings, skip AST-parsing
    # entirely. Compiled-regex alternation is ~100x faster than the Python
    # `any(n in source for n in needles)` loop on ~700-needle / ~700-file
    # repos.
    needles = set(by_name.keys()) | set(script_filename_for_module.values())
    if needles:
        # Sort by length desc so longer alternatives win; escape regex metas.
        prefilter_re = re.compile(
            "|".join(re.escape(n) for n in sorted(needles, key=len, reverse=True))
        )
    else:
        prefilter_re = None

    for path in _iter_source_files(scan_roots):
        try:
            source = path.read_text()
        except UnicodeDecodeError:
            continue
        if prefilter_re is not None and not prefilter_re.search(source):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        imports = _collect_imports(tree)

        # Single AST walk per file: each ast.Call gets checked for both
        # direct-call and subprocess-script signatures. (Original two-pass
        # variant did N_modules ast.walk()s per file via the subprocess
        # helper — quadratic.)
        # Pre-build module-by-script and apis-by-module for inner loops.
        apis_by_module: dict[str, list[ApiEntry]] = defaultdict(list)
        for api in apis:
            apis_by_module[api.module].append(api)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            # Direct-call leg
            bare = _bare_name(node)
            if bare is not None and bare in by_name:
                candidates = by_name[bare]
                resolved_mod = _resolved_module_for_call(node, imports)
                for api in candidates:
                    if api.file == path and node.lineno == api.lineno:
                        continue
                    if resolved_mod is not None and not (
                        resolved_mod == api.module
                        or api.module.endswith("." + resolved_mod)
                        or resolved_mod.endswith("." + api.module.split(".")[-1])
                    ):
                        continue
                    api.callers.append((path, node.lineno, "direct"))

            # Subprocess leg
            if _is_subprocess_call(node):
                strs = _subprocess_string_constants(node)
                if not strs:
                    continue
                for module, script_name in script_filename_for_module.items():
                    if not any(script_name in s for s in strs):
                        continue
                    key = (module, path, node.lineno)
                    if key in seen_subproc_per_module:
                        continue
                    seen_subproc_per_module.add(key)
                    for api in apis_by_module.get(module, []):
                        api.callers.append((path, node.lineno, "subprocess"))


# ── Output ─────────────────────────────────────────────────────────────────


def render_markdown(apis: list[ApiEntry]) -> str:
    """Emit the markdown index, sorted module → api → caller."""
    lines: list[str] = [
        "# Callmap",
        "",
        "Auto-generated by `devlab/claudecode/callmap.py`. Do not edit by hand.",
        "Re-run the tool to refresh; the output is committed to git so",
        "audit-check-fresh can detect drift.",
        "",
        "Forward axis (concept → location) lives in top-of-file canonical",
        "docstrings (docs-in-code rollout). This file is the reverse axis:",
        "function → who calls it.",
        "",
        "Conventions:",
        "- API surface: top-level non-`_` functions and class methods in",
        "  `devlab/claudecode/` and `devices/igor/tools/`. Override per-module",
        "  with `__api__ = [...]`. Annotate with `# API: <text>` above the def.",
        "- Caller kinds: `direct` (import + call) and `subprocess` (invocation",
        "  of the module's script file).",
        "- Known blind spots: dynamic dispatch (`getattr(mod, 'name')()`),",
        "  inheritance-resolved method calls.",
        "",
        "---",
        "",
    ]
    by_module: dict[str, list[ApiEntry]] = defaultdict(list)
    for api in apis:
        by_module[api.module].append(api)
    for module in sorted(by_module):
        lines.append(f"## `{module}`")
        lines.append("")
        for api in sorted(by_module[module], key=lambda a: a.name):
            header = f"### `{api.name}`"
            rel_def = _safe_rel(api.file)
            lines.append(header)
            lines.append("")
            lines.append(f"- **Defined:** `{rel_def}:{api.lineno}`")
            if api.description:
                lines.append(f"- **API:** {api.description}")
            if not api.callers:
                lines.append("- **Callers:** _(none found)_")
            else:
                # Sort + dedupe identical (file, line, kind)
                unique = sorted(set(api.callers), key=lambda c: (str(c[0]), c[1]))
                lines.append(f"- **Callers** ({len(unique)}):")
                for cfile, clineno, ckind in unique:
                    rel_call = _safe_rel(cfile)
                    marker = "" if ckind == "direct" else f" ({ckind})"
                    lines.append(f"  - `{rel_call}:{clineno}`{marker}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if regeneration would change the on-disk file (drift detection)",
    )
    parser.add_argument(
        "--source-roots",
        nargs="*",
        default=None,
        help="override SOURCE_ROOTS (paths)",
    )
    parser.add_argument(
        "--scan-roots",
        nargs="*",
        default=None,
        help="override SCAN_ROOTS (paths)",
    )
    parser.add_argument(
        "--output",
        default=str(OUTPUT_PATH),
        help="output markdown file (default docs/callmap.md)",
    )
    args = parser.parse_args()

    source_roots = (
        [Path(p) for p in args.source_roots]
        if args.source_roots
        else list(SOURCE_ROOTS)
    )
    scan_roots = (
        [Path(p) for p in args.scan_roots] if args.scan_roots else list(SCAN_ROOTS)
    )

    apis = discover_apis(source_roots)
    attach_callers(apis, scan_roots)
    rendered = render_markdown(apis)

    output_path = Path(args.output)
    if args.check:
        existing = output_path.read_text() if output_path.exists() else ""
        if existing != rendered:
            print(
                f"FAIL: callmap drift detected — regenerating "
                f"{output_path.relative_to(REPO_ROOT) if output_path.is_absolute() else output_path} "
                f"would change {abs(len(existing) - len(rendered))} bytes. "
                f"Re-run `python3 devlab/claudecode/callmap.py` and commit.",
                file=sys.stderr,
            )
            return 1
        print(f"PASS: callmap fresh ({len(apis)} APIs).")
        return 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered)
    print(
        f"Wrote {output_path.relative_to(REPO_ROOT) if output_path.is_absolute() else output_path} "
        f"({len(apis)} APIs, "
        f"{sum(len(a.callers) for a in apis)} caller refs)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
