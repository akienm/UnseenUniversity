"""
instance_tracker.py — T-instance-tracking-startup (#424)

Igor records his own boot and shutdown history so we always know which
commit / pid / host is running. Both file AND database:

  - JSONL at ~/.TheIgors/<instance>/instance_log.jsonl is the always-works
    fallback that survives Postgres downtime. Append-only.
  - instance_log table in Igor's home DB is the queryable surface.

Design principle (Akien 2026-04-13):
  ANYTHING QUERYABLE ON DEMAND DOESN'T NEED TO BE HOT IN TWM.
  Boot version is stable across a whole session. It doesn't change per turn
  and only matters when explicitly asked for. Reference, not working memory.
  record_startup() MUST NOT push to TWM. Verified by tests.

Format for the structured status string: yyyymmdd.hhmmssuuuuuu.xxxxxxx
  - yyyymmdd:        date
  - hhmmssuuuuuu:    time with microseconds (12 digits)
  - xxxxxxx:         short commit hash (7 chars)
  Example: 20260414.110000123456.abc1234
"""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
from datetime import datetime
from pathlib import Path

from ..paths import paths as _paths
from .registry import Tool, registry

logger = logging.getLogger(__name__)


def _git(args: list[str]) -> str:
    """Run a git command in the repo root. Empty string on failure."""
    try:
        repo_root = Path(__file__).resolve().parents[3]
        out = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=3,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception as exc:
        logger.warning("instance_tracker git %s failed: %s", args, exc)
        return ""


def _jsonl_path() -> Path:
    return _paths().instance / "instance_log.jsonl"


def _format_status(timestamp_iso: str, commit_short: str) -> str:
    """yyyymmdd.hhmmssuuuuuu.xxxxxxx — matches D256 timestamp ID format."""
    try:
        ts = datetime.fromisoformat(timestamp_iso)
    except ValueError:
        return f"????????.????????????.{commit_short or 'unknown'}"
    date_part = ts.strftime("%Y%m%d")
    time_part = ts.strftime("%H%M%S") + f"{ts.microsecond:06d}"
    return f"{date_part}.{time_part}.{commit_short or 'unknown'}"


def _write_jsonl(record: dict) -> None:
    """Append one record to the JSONL log. Best-effort; never raises."""
    try:
        path = _jsonl_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
    except Exception as exc:
        logger.warning("instance_tracker JSONL write failed: %s", exc)


def _write_db(cortex, record: dict) -> None:
    """Insert one record into instance_log table. Best-effort; never raises."""
    try:
        with cortex._db() as conn:
            conn.execute(
                "INSERT INTO instance_log "
                "(timestamp, event, instance_id, commit_short, commit_long, "
                "branch, host, pid, narrative) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    record["timestamp"],
                    record["event"],
                    record["instance_id"],
                    record["commit_short"],
                    record["commit_long"],
                    record["branch"],
                    record["host"],
                    record["pid"],
                    record["narrative"],
                ),
            )
    except Exception as exc:
        logger.warning("instance_tracker DB write failed: %s", exc)


def _build_record(event: str, instance_id: str, narrative: str = "") -> dict:
    return {
        "timestamp": datetime.now().isoformat(),
        "event": event,
        "instance_id": instance_id,
        "commit_short": _git(["rev-parse", "--short", "HEAD"]),
        "commit_long": _git(["rev-parse", "HEAD"]),
        "branch": _git(["rev-parse", "--abbrev-ref", "HEAD"]),
        "host": socket.gethostname(),
        "pid": os.getpid(),
        "narrative": narrative,
    }


def record_startup(cortex, instance_id: str, narrative: str = "boot") -> dict:
    """
    Called early in main.py boot path after Cortex is available.

    Writes the same record to JSONL and the instance_log table. Returns the
    record dict for test verification and optional logging.

    MUST NOT push to TWM. This is reference state, not working memory.
    """
    record = _build_record("boot", instance_id, narrative)
    _write_jsonl(record)
    _write_db(cortex, record)
    return record


def record_shutdown(cortex, instance_id: str, narrative: str = "shutdown") -> dict:
    """
    Called from signal handlers or atexit. Best-effort, never blocks shutdown.

    Also does not push to TWM.
    """
    record = _build_record("shutdown", instance_id, narrative)
    _write_jsonl(record)
    _write_db(cortex, record)
    return record


def _most_recent_boot(cortex) -> dict | None:
    """Return the most recent boot row, or None if the table is empty."""
    try:
        with cortex._db() as conn:
            conn.execute(
                "SELECT timestamp, event, instance_id, commit_short, commit_long, "
                "branch, host, pid, narrative "
                "FROM instance_log WHERE event = %s "
                "ORDER BY timestamp DESC LIMIT 1",
                ("boot",),
            )
            row = conn.fetchone()
            if not row:
                return None
            return {
                "timestamp": row[0],
                "event": row[1],
                "instance_id": row[2],
                "commit_short": row[3],
                "commit_long": row[4],
                "branch": row[5],
                "host": row[6],
                "pid": row[7],
                "narrative": row[8],
            }
    except Exception as exc:
        logger.warning("instance_tracker _most_recent_boot failed: %s", exc)
        return None


def igor_instance_current(cortex=None, **_) -> str:
    """
    Return the structured status string for the most recent boot record.

    Format: yyyymmdd.hhmmssuuuuuu.xxxxxxx

    Igor calls this when he needs to know what he's running. Not hot in TWM.
    """
    if cortex is None:
        return "instance_tracker: cortex not available in this call context"
    row = _most_recent_boot(cortex)
    if row is None:
        return "instance_tracker: no boot records yet"
    return _format_status(row["timestamp"], row["commit_short"] or "")


def igor_instance_history(cortex=None, limit: int = 10, **_) -> str:
    """
    Return recent boot/shutdown records as structured strings, newest first.

    One record per line: event:yyyymmdd.hhmmssuuuuuu.xxxxxxx branch host pid
    """
    if cortex is None:
        return "instance_tracker: cortex not available in this call context"
    try:
        lim = int(limit)
    except (TypeError, ValueError):
        lim = 10
    lim = max(1, min(lim, 100))
    try:
        with cortex._db() as conn:
            conn.execute(
                "SELECT timestamp, event, commit_short, branch, host, pid "
                "FROM instance_log ORDER BY timestamp DESC LIMIT %s",
                (lim,),
            )
            rows = conn.fetchall()
    except Exception as exc:
        logger.warning("instance_tracker igor_instance_history failed: %s", exc)
        return f"instance_tracker: history query failed: {exc}"

    if not rows:
        return "instance_tracker: no records yet"

    lines = []
    for row in rows:
        status = _format_status(row[0], row[2] or "")
        lines.append(
            f"{row[1]:<8} {status}  {row[3] or '?'}  {row[4] or '?'}  pid={row[5]}"
        )
    return "\n".join(lines)


# ── Tool registrations ────────────────────────────────────────────────────────
# These are the on-demand query path. record_startup / record_shutdown are
# wired into main.py directly and are not exposed as tools (they need cortex
# + instance_id and run during boot/shutdown, not from habit dispatch).

registry.register(
    Tool(
        name="igor_instance_current",
        description=(
            "Return the current Igor instance status string "
            "(yyyymmdd.hhmmssuuuuuu.xxxxxxx format). Use when asked what "
            "version/commit/instance is running right now."
        ),
        parameters={},
        fn=igor_instance_current,
    )
)

registry.register(
    Tool(
        name="igor_instance_history",
        description=(
            "Return recent Igor boot/shutdown records, newest first. "
            "Use when asked about uptime history or recent restarts."
        ),
        parameters={
            "limit": {
                "type": "integer",
                "description": "Max records to return (default 10, capped at 100).",
            }
        },
        fn=igor_instance_history,
    )
)
