"""
debug_extractor.py — Deterministic debug extraction layer for /debug skill.

Given a structured query (component, timestamp window, optional raw text),
extracts: log window, state snapshot, stack trace, error type.

Zero inference — all extraction is string parsing, file reads, subprocess
calls. Output is a stable dict that T-debug-skill (Haiku/Sonnet layer)
consumes for hypothesis generation.

D-debug-skill-2026-05-28
"""

from __future__ import annotations
from unseen_university._uu_root import uu_home

import os
import re
import subprocess
import traceback as tb
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── Log file mapping by component ──────────────────────────────────────────────

_IGOR_HOME = Path(uu_home())
_LOG_DIR = _IGOR_HOME / "logs"

_COMPONENT_LOGS: dict[str, list[str]] = {
    "pe_chain": ["tool_calls.log", "errors.log"],
    "ne": ["ne_runs.log", "errors.log"],
    "test": ["errors.log"],
    "schema": ["db_queries.log", "errors.log"],
    "scope_guard": ["scope_guard.log", "errors.log"],
    "general": ["errors.log", "tool_calls.log"],
}

# ── Error classifier patterns ──────────────────────────────────────────────────

_ERROR_PATTERNS: list[tuple[str, str]] = [
    (
        r"old_string.*not found|no match.*old_string|ValueError.*old_string",
        "pe_chain_old_string",
    ),
    (r"NE.*stuck|stuck.*NE|max.*cycles.*exceeded|ne_stuck", "ne_stuck"),
    (r"AssertionError|assert.*fail|FAILED|pytest.*fail", "test_failure"),
    (
        r"psycopg2\.errors|ProgrammingError.*SQL|column.*does not exist|relation.*does not exist",
        "schema_error",
    ),
    (r"HIGH.inertia|inertia.*block|scope_guard.*block", "scope_guard_block"),
    (r"basket\[.error.\]|pe_chain.*error|pe_.*failed", "pe_chain_error"),
    (r"TimeoutError|timeout.*exceeded|request.*timed out", "timeout"),
    (r"ImportError|ModuleNotFoundError|cannot import", "import_error"),
    (r"safe_mode.*trip|SAFE_MODE|degrade.*safe", "safe_mode_trip"),
]


def _parse_iso(ts: str) -> datetime | None:
    """Parse ISO timestamp string, return UTC-aware datetime or None."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(ts[:26], fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def extract_log_window(
    component: str,
    timestamp: str,
    window_minutes: int = 5,
    max_lines: int = 30,
) -> list[str]:
    """Return up to max_lines log lines within window_minutes of timestamp.

    Searches component-specific log files in order, returns first match set.
    Empty list when no log files exist or no lines fall in the window.
    """
    center = _parse_iso(timestamp)
    if center is None:
        return []

    start = center - timedelta(minutes=window_minutes)
    end = center + timedelta(minutes=window_minutes)

    log_names = _COMPONENT_LOGS.get(component, _COMPONENT_LOGS["general"])
    for log_name in log_names:
        log_path = _LOG_DIR / log_name
        if not log_path.exists():
            continue
        try:
            lines = log_path.read_text(errors="replace").splitlines()
        except OSError:
            continue

        # Collect lines with timestamps in window, plus non-timestamped continuation lines
        window_lines: list[str] = []
        in_window = False
        for line in lines:
            ts_match = re.match(r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})", line)
            if ts_match:
                line_dt = _parse_iso(ts_match.group(1))
                in_window = line_dt is not None and start <= line_dt <= end
            if in_window:
                window_lines.append(line)
                if len(window_lines) >= max_lines:
                    break

        if window_lines:
            return window_lines

    return []


def extract_state_snapshot(component: str, text: str) -> dict[str, Any]:
    """Extract component-specific state fields from raw log text.

    Returns a dict with the most diagnostic fields for each component type.
    Fields absent from the text are omitted from the dict.
    """
    snapshot: dict[str, Any] = {}

    if component == "pe_chain":
        # basket fields: ticket_id, phase, error, plan_files, hypotheses count
        for field in ("ticket_id", "phase", "error", "plan_summary"):
            m = re.search(rf"basket\[.{field}.\]\s*[:=]\s*(.+?)(?:\n|$)", text)
            if m:
                snapshot[field] = m.group(1).strip()
        m = re.search(r"HYPOTHESIZE produced (\d+) edit", text)
        if m:
            snapshot["hypothesis_count"] = int(m.group(1))

    elif component == "ne":
        # NE fields: goal, cycle count, last narrative fragment
        m = re.search(r"goal\s*[:=]\s*(.{1,80})", text, re.I)
        if m:
            snapshot["goal"] = m.group(1).strip()
        m = re.search(r"cycle[_ ]?(\d+)", text, re.I)
        if m:
            snapshot["cycle"] = int(m.group(1))
        m = re.search(r"NARRATIVE\s*:\s*(.{1,120})", text)
        if m:
            snapshot["last_narrative"] = m.group(1).strip()

    elif component == "test":
        # Test failure: test name, assertion line
        m = re.search(r"FAILED (.+?)(?:\s|$)", text)
        if m:
            snapshot["failed_test"] = m.group(1).strip()
        m = re.search(r"AssertionError[: ]+(.{1,120})", text)
        if m:
            snapshot["assertion"] = m.group(1).strip()
        m = re.search(r"assert (.{1,80})", text)
        if m:
            snapshot["assert_expr"] = m.group(1).strip()

    elif component == "schema":
        # Schema error: SQL snippet, table name, error message
        m = re.search(r'(?:column|relation|table)\s+"?(\w+)"?', text, re.I)
        if m:
            snapshot["object_name"] = m.group(1)
        m = re.search(r"ERROR\s+(.{1,120})", text)
        if m:
            snapshot["db_error"] = m.group(1).strip()

    return snapshot


def parse_stack_trace(text: str) -> list[dict[str, Any]]:
    """Extract file/line/function entries from a Python traceback.

    Returns list of {file, line, function} dicts, innermost last (call order).
    """
    frames: list[dict[str, Any]] = []
    # Standard Python traceback format: '  File "path", line N, in function'
    pattern = re.compile(r'File "([^"]+)", line (\d+), in (\w+)')
    for m in pattern.finditer(text):
        frames.append(
            {
                "file": m.group(1),
                "line": int(m.group(2)),
                "function": m.group(3),
            }
        )
    return frames


def classify_error(text: str) -> str:
    """Map error text to a known error type string.

    Returns one of: pe_chain_old_string, ne_stuck, test_failure, schema_error,
    scope_guard_block, pe_chain_error, timeout, import_error, safe_mode_trip, unknown.
    """
    for pattern, error_type in _ERROR_PATTERNS:
        if re.search(pattern, text, re.I):
            return error_type
    return "unknown"


def run_test_extract(
    ticket_id: str | None = None, test_path: str | None = None
) -> dict[str, Any]:
    """Run pytest on the specified path and return structured output.

    Returns: {passed: bool, total: int, failed: int, failures: [{test, msg}]}.
    Graceful degradation: returns {error: str} if pytest is unavailable.
    """
    cmd = ["python", "-m", "pytest", "-q", "--tb=short"]
    if test_path:
        cmd.append(test_path)
    else:
        cmd.extend(["tests/", "-x"])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(Path.home() / "dev" / "src" / "UnseenUniversity"),
        )
        output = result.stdout + result.stderr
        failures = []
        for m in re.finditer(r"FAILED (.+?)(?:\n|$)", output):
            test_name = m.group(1).strip()
            # Find the error message after the FAILED line
            msg_m = re.search(
                rf"{re.escape(test_name)}.*?AssertionError[: ]+(.{{1,120}})",
                output,
                re.S,
            )
            failures.append(
                {"test": test_name, "msg": msg_m.group(1).strip() if msg_m else ""}
            )

        m_summary = re.search(r"(\d+) passed", output)
        m_failed = re.search(r"(\d+) failed", output)
        total = int(m_summary.group(1)) if m_summary else 0
        n_failed = int(m_failed.group(1)) if m_failed else len(failures)
        return {
            "passed": result.returncode == 0,
            "total": total + n_failed,
            "failed": n_failed,
            "failures": failures,
        }
    except subprocess.TimeoutExpired:
        return {"error": "pytest timed out after 60s"}
    except Exception as e:
        return {"error": str(e)}


# ── Public API ─────────────────────────────────────────────────────────────────


def extract(query: dict[str, Any]) -> dict[str, Any]:
    """Main entry point — given a structured query, return a debug extraction dict.

    Query keys:
        component   str   pe_chain | ne | test | schema | general
        timestamp   str   ISO 8601 — center of the log window
        window_min  int   minutes either side of timestamp (default 5)
        ticket_id   str   optional — for test runner targeting
        text        str   optional raw pasted text (overrides log file search)

    Returns:
        log_window      list[str]       extracted log lines
        state_snapshot  dict            component-specific fields
        stack_trace     list[dict]      file/line/function frames
        error_type      str             classified error
        raw_error       str             first error line found
    """
    component = query.get("component", "general")
    timestamp = query.get("timestamp", "")
    window_min = int(query.get("window_min", 5))
    text = query.get("text", "")

    # Use provided text as log source, or fetch from log files
    if text:
        log_window = text.splitlines()[:30]
        full_text = text
    elif timestamp:
        log_window = extract_log_window(component, timestamp, window_min)
        full_text = "\n".join(log_window)
    else:
        log_window = []
        full_text = ""

    state_snapshot = extract_state_snapshot(component, full_text)
    stack_trace = parse_stack_trace(full_text)
    error_type = classify_error(full_text)

    # First error line
    raw_error = ""
    for line in log_window:
        if re.search(r"ERROR|FAILED|Exception|Traceback", line, re.I):
            raw_error = line.strip()
            break

    return {
        "log_window": log_window,
        "state_snapshot": state_snapshot,
        "stack_trace": stack_trace,
        "error_type": error_type,
        "raw_error": raw_error,
    }
