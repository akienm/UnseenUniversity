"""system_alarms — STUB (proof scaffold for T-system-alarms-primitive).

Symbols resolve so the proof test imports cleanly and fails on a *behavioral*
assertion (no dedup, no file, no archive) rather than a collection error — the
stub-first proof-on-close convention. The real implementation lands in the
following commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

DEFAULT_CALLER_QUIET = timedelta(hours=24)


def alarms_dir() -> Path:  # pragma: no cover - stub
    return Path("/nonexistent/system_alarms")


def archive_dir() -> Path:  # pragma: no cover - stub
    return alarms_dir() / "archive"


def _atomic_write(path: Path, payload: dict) -> None:  # pragma: no cover - stub
    pass


@dataclass
class AlarmResult:
    signature: str
    status: str
    count: int


def raise_alarm(
    signature: str,
    caller: str,
    message: str,
    *,
    level: str = "ERROR",
    emit_log: bool = True,
    now: Optional[datetime] = None,
) -> AlarmResult:  # pragma: no cover - stub
    return AlarmResult(signature=signature, status="new", count=0)


def get_alarm(signature: str) -> Optional[dict]:  # pragma: no cover - stub
    return None


def list_alarms() -> list[dict]:  # pragma: no cover - stub
    return []


def list_archived() -> list[dict]:  # pragma: no cover - stub
    return []


def close_alarm(signature: str, *, now: Optional[datetime] = None) -> bool:  # pragma: no cover - stub
    return False


def prune_stale(
    *,
    now: Optional[datetime] = None,
    caller_quiet: timedelta = DEFAULT_CALLER_QUIET,
) -> dict:  # pragma: no cover - stub
    return {"callers_pruned": 0, "alarms_aged_out": 0}
