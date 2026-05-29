#!/usr/bin/env python3
"""
audit_logging.py — T-detailed-logging-audit

Callsite-level inventory of logging statements across the codebase. Two
passes:

1. STATIC (default): AST walk over devices/igor/ (UnseenUniversity), lab/utility_closet/, lab/.
   For every logging callsite, classify the pattern and resolve the host
   class. Cross-check inheritance against IgorBase/AgentBase via shared
   logic with audit_check_igorbase.py.

2. RUNTIME (--runtime): Walk ~/.TheIgors/logs/ + ~/.unseen_university/logs/
   over a window. Per logger, count lines, compute rate. Flag noisy/quiet
   sources and slot-misrouted writes.

Output: lab/claudecode/reports/logging_audit_<ts>.md

Patterns classified (severity in parens):
  - GOOD       self.log.<level>(...)              (proper inherited)
  - GOOD       get_logger(__name__).<level>(...)  (acceptable module-level)
  - GOOD       log_error(...)                     (forensic logger — explicit)
  - LEGACY     _log.<level>(...)                  (pre-base-class extraction)
  - BYPASS     logging.getLogger(...).            (bypasses base class)
  - SMELL      print(...)                         (non-CLI use)

Goal: produce a punch list driving the migration to the universal
unseen_university base class + rackmount-slot logging pattern. Migration
itself ships as separate tickets, queued after this audit.

Inertia: LOW — read-only audit script. Writes report; does not edit code.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parents[2]

# Reuse the inheritance check from audit_check_igorbase.
sys.path.insert(0, str(REPO_ROOT / "lab" / "claudecode"))
from audit_check_igorbase import (  # noqa: E402
    EXEMPT_CLASS_NAMES,
    KNOWN_IGORBASE_ANCESTORS,
    THIRD_PARTY_BASES,
    _base_name,
    _build_local_parents,
    _has_igorbase_transitively,
)

# Roots to scan in static pass (within the TheIgors repo)
SCAN_ROOTS = (
    Path("/home/akien/dev/src/UnseenUniversity") / "devices" / "igor",
    REPO_ROOT / "lab" / "utility_closet",
    REPO_ROOT / "lab" / "claudecode",
)

# Excluded paths (vendored, generated, archived rebuild scripts).
# `archive` covers lab/claudecode/archive/ — one-shot seed scripts kept for
# DB rebuild capability per the README warning. Do not migrate; do not flag.
EXCLUDED_PARTS = {
    "__pycache__",
    "ebook_drm",
    "venv",
    ".venv",
    "node_modules",
    "archive",
}

# Test files: included but flagged separately (test scaffolding has different
# rules than production code).
TEST_DIR_PARTS = {"tests", "test"}

# Severity levels
SEV_GOOD = "good"
SEV_LEGACY = "legacy"
SEV_BYPASS = "bypass"
SEV_SMELL = "smell"

# Pattern categories
PATTERN_SELF_LOG = "self.log"
PATTERN_GET_LOGGER = "get_logger()"
PATTERN_LOG_ERROR = "log_error()"
PATTERN_LEGACY_LOG = "_log"
PATTERN_LOGGING_GETLOGGER = "logging.getLogger"
PATTERN_PRINT = "print()"

PATTERN_SEVERITY = {
    PATTERN_SELF_LOG: SEV_GOOD,
    PATTERN_GET_LOGGER: SEV_GOOD,
    PATTERN_LOG_ERROR: SEV_GOOD,
    PATTERN_LEGACY_LOG: SEV_LEGACY,
    PATTERN_LOGGING_GETLOGGER: SEV_BYPASS,
    PATTERN_PRINT: SEV_SMELL,
}

# Logging method names to look for after a Logger-like attribute
_LOG_METHOD_NAMES = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical"}
)


@dataclass
class Callsite:
    file: str
    line: int
    pattern: str
    severity: str
    enclosing_class: Optional[str]  # None = module-level
    enclosing_function: Optional[str]
    in_test: bool
    snippet: str = ""

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "pattern": self.pattern,
            "severity": self.severity,
            "enclosing_class": self.enclosing_class,
            "enclosing_function": self.enclosing_function,
            "in_test": self.in_test,
            "snippet": self.snippet,
        }


@dataclass
class ClassFinding:
    file: str
    line: int
    name: str
    bases: list[str]
    inherits_igorbase: bool
    in_test: bool

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "line": self.line,
            "name": self.name,
            "bases": self.bases,
            "inherits_igorbase": self.inherits_igorbase,
            "in_test": self.in_test,
        }


@dataclass
class FileResult:
    path: str
    callsites: list[Callsite] = field(default_factory=list)
    classes: list[ClassFinding] = field(default_factory=list)
    parse_error: Optional[str] = None


# ── AST helpers ───────────────────────────────────────────────────────────


def _is_excluded(path: Path) -> bool:
    return any(p in EXCLUDED_PARTS for p in path.parts)


def _is_test_file(path: Path) -> bool:
    name = path.name
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    if any(p in TEST_DIR_PARTS for p in path.parts):
        return True
    return False


def _is_cli_entrypoint(tree: ast.AST) -> bool:
    """True if the file has an `if __name__ == '__main__':` guard at top level."""
    for node in tree.body if isinstance(tree, ast.Module) else []:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # Match `__name__ == "__main__"` either direction
        if (
            isinstance(test, ast.Compare)
            and len(test.ops) == 1
            and isinstance(test.ops[0], ast.Eq)
        ):
            left, right = test.left, test.comparators[0]
            for a, b in ((left, right), (right, left)):
                if (
                    isinstance(a, ast.Name)
                    and a.id == "__name__"
                    and isinstance(b, ast.Constant)
                    and b.value == "__main__"
                ):
                    return True
    return False


_LOG_VAR_NAMES = frozenset({"_log", "logger", "_logger", "log"})


def _scan_logger_assignments(tree: ast.AST) -> dict[str, str]:
    """Map module-level `<var> = <factory>(...)` to factory name.

    Returns {var_name: 'get_logger' | 'logging.getLogger' | 'unknown'} so
    classify_call can distinguish a `_log.warning(...)` whose `_log` came
    from get_logger (good) vs. logging.getLogger (legacy).
    """
    out: dict[str, str] = {}
    if not isinstance(tree, ast.Module):
        return out
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        var = node.targets[0].id
        if var not in _LOG_VAR_NAMES:
            continue
        if not isinstance(node.value, ast.Call):
            continue
        factory = node.value.func
        if isinstance(factory, ast.Name) and factory.id == "get_logger":
            out[var] = "get_logger"
        elif (
            isinstance(factory, ast.Attribute)
            and factory.attr == "getLogger"
            and isinstance(factory.value, ast.Name)
            and factory.value.id == "logging"
        ):
            out[var] = "logging.getLogger"
        else:
            out[var] = "unknown"
    return out


def _classify_call(
    node: ast.Call, logger_assignments: Optional[dict[str, str]] = None
) -> Optional[str]:
    """Return the pattern name for a call node, or None if not a logging call.

    `logger_assignments` (optional) tells us which module-level log-var
    names came from `get_logger(...)` (good) vs `logging.getLogger(...)`
    (legacy). When omitted, all log-var calls fall through to LEGACY for
    backward-compat with existing call sites and tests.
    """
    func = node.func
    assignments = logger_assignments or {}

    # print(...)
    if isinstance(func, ast.Name) and func.id == "print":
        return PATTERN_PRINT

    # log_error(...)
    if isinstance(func, ast.Name) and func.id == "log_error":
        return PATTERN_LOG_ERROR

    # logging.getLogger(...) — direct call (not via get_logger helper)
    if isinstance(func, ast.Attribute) and func.attr == "getLogger":
        if isinstance(func.value, ast.Name) and func.value.id == "logging":
            return PATTERN_LOGGING_GETLOGGER

    # get_logger(...) — module-level helper
    if isinstance(func, ast.Name) and func.id == "get_logger":
        return PATTERN_GET_LOGGER

    # <something>.<level>(...) — log methods
    if isinstance(func, ast.Attribute) and func.attr in _LOG_METHOD_NAMES:
        target = func.value
        # self.log.<level>(...)
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "log"
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
        ):
            return PATTERN_SELF_LOG
        # <name>.<level>(...) — check the assignment table
        if isinstance(target, ast.Name) and target.id in _LOG_VAR_NAMES:
            factory = assignments.get(target.id)
            if factory == "get_logger":
                return PATTERN_GET_LOGGER  # _log = get_logger(...) is the canonical pattern
            return PATTERN_LEGACY_LOG  # logging.getLogger or unknown source
        # get_logger(__name__).<level>(...) — chained call
        if isinstance(target, ast.Call):
            inner = target.func
            if isinstance(inner, ast.Name) and inner.id == "get_logger":
                return PATTERN_GET_LOGGER
            if (
                isinstance(inner, ast.Attribute)
                and inner.attr == "getLogger"
                and isinstance(inner.value, ast.Name)
                and inner.value.id == "logging"
            ):
                return PATTERN_LOGGING_GETLOGGER

    return None


class _CallsiteWalker(ast.NodeVisitor):
    """AST walker that records logging callsites with class/function context."""

    def __init__(
        self,
        source_lines: list[str],
        path_str: str,
        in_test: bool,
        logger_assignments: Optional[dict[str, str]] = None,
    ):
        self.source_lines = source_lines
        self.path_str = path_str
        self.in_test = in_test
        self.logger_assignments = logger_assignments or {}
        self.callsites: list[Callsite] = []
        self._class_stack: list[str] = []
        self._func_stack: list[str] = []

    def _snippet(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.source_lines):
            return self.source_lines[lineno - 1].strip()[:120]
        return ""

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self.generic_visit(node)
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._func_stack.append(node.name)
        self.generic_visit(node)
        self._func_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        pattern = _classify_call(node, self.logger_assignments)
        if pattern:
            severity = PATTERN_SEVERITY[pattern]
            self.callsites.append(
                Callsite(
                    file=self.path_str,
                    line=node.lineno,
                    pattern=pattern,
                    severity=severity,
                    enclosing_class=(
                        self._class_stack[-1] if self._class_stack else None
                    ),
                    enclosing_function=(
                        self._func_stack[-1] if self._func_stack else None
                    ),
                    in_test=self.in_test,
                    snippet=self._snippet(node.lineno),
                )
            )
        self.generic_visit(node)


def _collect_classes(tree: ast.AST, path_str: str, in_test: bool) -> list[ClassFinding]:
    """Find ClassDefs and check inheritance against IgorBase/AgentBase."""
    local_parents = _build_local_parents(tree)
    findings: list[ClassFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if node.name.startswith("_"):
            continue
        if node.name in EXEMPT_CLASS_NAMES:
            continue
        bases = [_base_name(b) for b in node.bases]

        # Decorator-based exemptions (dataclass)
        is_dataclass = any(
            (isinstance(d, ast.Name) and d.id == "dataclass")
            or (
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Name)
                and d.func.id == "dataclass"
            )
            for d in node.decorator_list
        )
        if is_dataclass:
            continue

        inherits = (
            "IgorBase" in bases
            or "AgentBase" in bases
            or _has_igorbase_transitively(node.name, local_parents)
            or any(b in KNOWN_IGORBASE_ANCESTORS for b in bases)
        )

        # Bases are exclusively third-party — exempt
        if bases and all(b in THIRD_PARTY_BASES for b in bases):
            continue

        findings.append(
            ClassFinding(
                file=path_str,
                line=node.lineno,
                name=node.name,
                bases=bases,
                inherits_igorbase=inherits,
                in_test=in_test,
            )
        )
    return findings


def audit_file(path: Path) -> FileResult:
    """Run static analysis on a single file."""
    rel = str(path.relative_to(REPO_ROOT))
    in_test = _is_test_file(path)
    result = FileResult(path=rel)
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError) as exc:
        result.parse_error = str(exc)
        return result

    is_cli = _is_cli_entrypoint(tree)
    logger_assignments = _scan_logger_assignments(tree)
    walker = _CallsiteWalker(
        source.splitlines(), rel, in_test, logger_assignments=logger_assignments
    )
    walker.visit(tree)
    callsites = walker.callsites

    # Suppress print() smell flag for CLI entrypoints — these are scripts where
    # stdout output is the design intent (seed scripts, migrations, ops tools).
    # The `if __name__ == "__main__":` guard signals CLI; all prints in such a
    # file are exempt regardless of enclosing function.
    if is_cli:
        callsites = [c for c in callsites if c.pattern != PATTERN_PRINT]

    result.callsites = callsites
    result.classes = _collect_classes(tree, rel, in_test)
    return result


def audit_static(roots: list[Path]) -> list[FileResult]:
    """Run static audit across all .py files under given roots."""
    results: list[FileResult] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if _is_excluded(path):
                continue
            results.append(audit_file(path))
    return results


# ── Runtime pass ──────────────────────────────────────────────────────────


def audit_runtime(log_dirs: list[Path], window_minutes: int = 240) -> dict:
    """Walk log files; per-logger line counts within window.

    Returns dict with:
      window_minutes, log_dirs (paths scanned),
      per_logger: {logger_name: {lines, rate_per_min, file, last_modified}},
      noisy: list of (logger, rate),
      quiet: list of (logger, lines) where lines < 1,
    """
    cutoff = datetime.now(timezone.utc).timestamp() - window_minutes * 60
    per_logger: dict[str, dict] = {}

    for log_dir in log_dirs:
        if not log_dir.exists():
            continue
        for log_path in sorted(log_dir.rglob("*.log")):
            try:
                stat = log_path.stat()
            except OSError:
                continue
            if stat.st_mtime < cutoff:
                continue
            logger_name = log_path.stem
            try:
                lines_in_window = _count_recent_lines(log_path, cutoff)
            except OSError as exc:
                lines_in_window = 0
                err = str(exc)
            else:
                err = None
            per_logger[logger_name] = {
                "lines": lines_in_window,
                "rate_per_min": round(lines_in_window / max(window_minutes, 1), 2),
                "file": str(log_path),
                "last_modified": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
                "error": err,
            }

    noisy = sorted(
        [
            (k, v["rate_per_min"])
            for k, v in per_logger.items()
            if v["rate_per_min"] > 50
        ],
        key=lambda kv: kv[1],
        reverse=True,
    )
    quiet = [(k, v["lines"]) for k, v in per_logger.items() if v["lines"] == 0]

    return {
        "window_minutes": window_minutes,
        "log_dirs": [str(d) for d in log_dirs],
        "per_logger": per_logger,
        "noisy": noisy,
        "quiet": quiet,
    }


_TS_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}")


def _count_recent_lines(log_path: Path, cutoff_ts: float) -> int:
    """Best-effort: count lines newer than cutoff_ts.

    Strategy: read the tail of the file (last 2MB), count lines that parse to a
    timestamp ≥ cutoff. For files smaller than 2MB just count all lines whose
    leading timestamp is ≥ cutoff. Lines without a parseable timestamp are
    counted (fall-through: assume they belong to the most recent timestamped
    block).
    """
    cutoff_dt = datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).replace(tzinfo=None)
    size = log_path.stat().st_size
    chunk = min(size, 2 * 1024 * 1024)
    with log_path.open("rb") as f:
        if size > chunk:
            f.seek(-chunk, 2)
            f.readline()  # discard partial line
        data = f.read().decode("utf-8", errors="replace")

    count = 0
    counting = False  # flips to True after first in-window timestamp
    for line in data.splitlines():
        m = _TS_PREFIX.match(line)
        if m:
            try:
                ts = datetime.fromisoformat(m.group(0).replace("T", " "))
            except ValueError:
                if counting:
                    count += 1
                continue
            if ts >= cutoff_dt:
                counting = True
                count += 1
            else:
                counting = False
        elif counting:
            count += 1
    return count


# ── Reporting ─────────────────────────────────────────────────────────────


def aggregate(file_results: list[FileResult]) -> dict:
    """Aggregate counts and groupings for the report."""
    pattern_counts: Counter = Counter()
    severity_counts: Counter = Counter()
    by_file: dict[str, list[Callsite]] = defaultdict(list)
    classes_missing_inh: list[ClassFinding] = []
    classes_total = 0
    parse_errors: list[tuple[str, str]] = []
    for fr in file_results:
        if fr.parse_error:
            parse_errors.append((fr.path, fr.parse_error))
            continue
        for cs in fr.callsites:
            pattern_counts[cs.pattern] += 1
            severity_counts[cs.severity] += 1
            by_file[cs.file].append(cs)
        for cf in fr.classes:
            classes_total += 1
            if not cf.inherits_igorbase and not cf.in_test:
                classes_missing_inh.append(cf)
    return {
        "pattern_counts": dict(pattern_counts),
        "severity_counts": dict(severity_counts),
        "by_file": dict(by_file),
        "classes_missing_inh": classes_missing_inh,
        "classes_total": classes_total,
        "parse_errors": parse_errors,
        "files_scanned": len([fr for fr in file_results if not fr.parse_error]),
    }


def write_report(
    static_agg: dict,
    runtime_data: Optional[dict],
    out_path: Path,
) -> None:
    """Write a markdown report summarizing the audit."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines.append(f"# Logging audit — {ts}")
    lines.append("")
    lines.append(f"Files scanned: {static_agg['files_scanned']}")
    lines.append(f"Classes scanned: {static_agg['classes_total']}")
    lines.append("")

    # Severity summary
    sev = static_agg["severity_counts"]
    lines.append("## Severity summary")
    lines.append("")
    lines.append("| Severity | Count |")
    lines.append("|---|---|")
    for s in (SEV_GOOD, SEV_LEGACY, SEV_BYPASS, SEV_SMELL):
        lines.append(f"| {s} | {sev.get(s, 0)} |")
    lines.append("")

    # Pattern breakdown
    pat = static_agg["pattern_counts"]
    lines.append("## Pattern breakdown")
    lines.append("")
    lines.append("| Pattern | Count | Severity |")
    lines.append("|---|---|---|")
    for p, c in sorted(pat.items(), key=lambda kv: -kv[1]):
        lines.append(f"| `{p}` | {c} | {PATTERN_SEVERITY[p]} |")
    lines.append("")

    # Top files by non-good count
    by_file = static_agg["by_file"]
    file_scores: list[tuple[str, int, int]] = []
    for fp, sites in by_file.items():
        bad = sum(1 for s in sites if s.severity != SEV_GOOD)
        file_scores.append((fp, bad, len(sites)))
    file_scores.sort(key=lambda t: -t[1])
    lines.append("## Top 30 files by non-good callsite count")
    lines.append("")
    lines.append("| File | Non-good | Total |")
    lines.append("|---|---|---|")
    for fp, bad, total in file_scores[:30]:
        if bad == 0:
            continue
        lines.append(f"| `{fp}` | {bad} | {total} |")
    lines.append("")

    # Classes missing inheritance
    missing = static_agg["classes_missing_inh"]
    lines.append(f"## Classes missing IgorBase/AgentBase inheritance ({len(missing)})")
    lines.append("")
    if missing:
        lines.append("| File | Line | Class | Bases |")
        lines.append("|---|---|---|---|")
        for cf in missing[:80]:
            bases = ", ".join(cf.bases) if cf.bases else "(none)"
            lines.append(f"| `{cf.file}` | {cf.line} | `{cf.name}` | {bases} |")
        if len(missing) > 80:
            lines.append(f"\n_... and {len(missing) - 80} more_")
    lines.append("")

    # Per-file callsite tables for top files
    lines.append("## Per-file callsite detail (top 15)")
    lines.append("")
    for fp, bad, total in file_scores[:15]:
        if bad == 0:
            continue
        sites = sorted(by_file[fp], key=lambda c: c.line)
        lines.append(f"### `{fp}`")
        lines.append("")
        lines.append("| Line | Pattern | Severity | Class | Function | Snippet |")
        lines.append("|---|---|---|---|---|---|")
        for cs in sites:
            klass = cs.enclosing_class or "(module)"
            func = cs.enclosing_function or "(top-level)"
            snip = cs.snippet.replace("|", "\\|")
            lines.append(
                f"| {cs.line} | `{cs.pattern}` | {cs.severity} | {klass} | {func} | `{snip}` |"
            )
        lines.append("")

    # Parse errors
    pe = static_agg["parse_errors"]
    if pe:
        lines.append(f"## Parse errors ({len(pe)})")
        lines.append("")
        for fp, err in pe[:20]:
            lines.append(f"- `{fp}`: {err}")
        lines.append("")

    # Runtime section
    if runtime_data:
        lines.append("## Runtime pass")
        lines.append("")
        lines.append(f"Window: last {runtime_data['window_minutes']} minutes")
        lines.append("")
        lines.append("Log dirs scanned:")
        for d in runtime_data["log_dirs"]:
            lines.append(f"- `{d}`")
        lines.append("")
        if runtime_data["noisy"]:
            lines.append("### Noisy loggers (>50 lines/min)")
            lines.append("")
            lines.append("| Logger | Rate (lines/min) |")
            lines.append("|---|---|")
            for lg, rate in runtime_data["noisy"]:
                lines.append(f"| `{lg}` | {rate} |")
            lines.append("")
        if runtime_data["quiet"]:
            lines.append("### Quiet loggers (0 lines in window)")
            lines.append("")
            for lg, _ in runtime_data["quiet"]:
                lines.append(f"- `{lg}`")
            lines.append("")
        lines.append("### Per-logger volume")
        lines.append("")
        lines.append("| Logger | Lines | Rate/min | File |")
        lines.append("|---|---|---|---|")
        for lg, info in sorted(
            runtime_data["per_logger"].items(),
            key=lambda kv: -kv[1]["lines"],
        )[:50]:
            lines.append(
                f"| `{lg}` | {info['lines']} | {info['rate_per_min']} | `{info['file']}` |"
            )
        lines.append("")

    out_path.write_text("\n".join(lines))


# ── Migration grouping (for ticket-filing helper) ─────────────────────────


def group_for_migration(static_agg: dict) -> dict:
    """Group findings into ticket-shaped buckets.

    Returns dict with keys: print_smell, logging_bypass, legacy_log,
    missing_inheritance — each maps to a list of files.
    """
    by_file = static_agg["by_file"]
    print_smell = defaultdict(list)
    logging_bypass = defaultdict(list)
    legacy_log = defaultdict(list)
    for fp, sites in by_file.items():
        for cs in sites:
            if cs.in_test:
                continue
            if cs.pattern == PATTERN_PRINT:
                print_smell[fp].append(cs.line)
            elif cs.pattern == PATTERN_LOGGING_GETLOGGER:
                logging_bypass[fp].append(cs.line)
            elif cs.pattern == PATTERN_LEGACY_LOG:
                legacy_log[fp].append(cs.line)
    missing_inh = defaultdict(list)
    for cf in static_agg["classes_missing_inh"]:
        missing_inh[cf.file].append(
            {"line": cf.line, "class": cf.name, "bases": cf.bases}
        )
    return {
        "print_smell": dict(print_smell),
        "logging_bypass": dict(logging_bypass),
        "legacy_log": dict(legacy_log),
        "missing_inheritance": dict(missing_inh),
    }


# ── CLI ───────────────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--runtime",
        action="store_true",
        help="Also run runtime log volume pass",
    )
    parser.add_argument(
        "--window-minutes",
        type=int,
        default=240,
        help="Runtime window in minutes (default 240 = 4h)",
    )
    parser.add_argument(
        "--log-dir",
        action="append",
        default=None,
        help="Additional runtime log dir to scan (repeatable)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output report path. Default: lab/claudecode/reports/logging_audit_<ts>.md",
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="Optional JSON dump of full grouping for ticket-filing helper",
    )
    args = parser.parse_args(argv)

    print(f"Scanning {len(SCAN_ROOTS)} roots...", file=sys.stderr)
    file_results = audit_static(list(SCAN_ROOTS))
    static_agg = aggregate(file_results)
    print(
        f"Static: {static_agg['files_scanned']} files, "
        f"{sum(static_agg['pattern_counts'].values())} callsites, "
        f"{len(static_agg['classes_missing_inh'])} classes missing inheritance",
        file=sys.stderr,
    )

    runtime_data = None
    if args.runtime:
        log_dirs = [
            Path.home() / ".unseen_university" / "logs",
        ]
        if args.log_dir:
            log_dirs.extend(Path(d) for d in args.log_dir)
        runtime_data = audit_runtime(log_dirs, args.window_minutes)
        print(
            f"Runtime: {len(runtime_data['per_logger'])} loggers, "
            f"{len(runtime_data['noisy'])} noisy, {len(runtime_data['quiet'])} quiet",
            file=sys.stderr,
        )

    if args.out is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        args.out = (
            REPO_ROOT / "lab" / "claudecode" / "reports" / f"logging_audit_{ts}.md"
        )
    write_report(static_agg, runtime_data, args.out)
    print(f"Wrote report: {args.out}", file=sys.stderr)

    if args.json:
        groups = group_for_migration(static_agg)
        # Convert Callsite objects to plain dicts via to_dict where needed
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(groups, indent=2))
        print(f"Wrote groups JSON: {args.json}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
