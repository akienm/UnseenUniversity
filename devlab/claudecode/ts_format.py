"""
ts_format.py — Canonical timestamp formatting for TheIgors project.

Canonical human-readable format: YYYY-MM-DD HH:MM:SS (local time)
Canonical filename-safe format:   YYYYMMDD.HHMMSS (local time)

Design rules:
- Human display: local time, no trailing Z, no UTC offset
- Filenames/slugs: YYYYMMDD.HHMMSS (period separator, filesystem-safe)
- Machine-to-machine / DB storage: ISO 8601 UTC (retain existing)
- UTC framing was a source of confusion (T-timestamp-format-normalization 2026-04-29)

Usage:
    from devlab.claudecode.ts_format import format_display, format_slug, parse_iso

    # UTC ISO from DB/transcript → local display
    dt = parse_iso("2026-04-29T19:34:56Z")
    format_display(dt)   # "2026-04-29 13:34:56"  (Mountain time)
    format_slug(dt)      # "20260429.133456"
"""

from __future__ import annotations

from datetime import datetime, timezone


def parse_iso(ts: str) -> datetime:
    """Parse an ISO 8601 timestamp string (with or without Z / +offset) to datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def to_local(dt: datetime) -> datetime:
    """Convert an aware datetime to local time."""
    return dt.astimezone()


def format_display(dt: datetime) -> str:
    """Return human-readable local timestamp: YYYY-MM-DD HH:MM:SS"""
    return to_local(dt).strftime("%Y-%m-%d %H:%M:%S")


def format_slug(dt: datetime) -> str:
    """Return filename-safe local timestamp: YYYYMMDD.HHMMSS"""
    return to_local(dt).strftime("%Y%m%d.%H%M%S")


def now_display() -> str:
    """Current local time as YYYY-MM-DD HH:MM:SS."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_slug() -> str:
    """Current local time as YYYYMMDD.HHMMSS."""
    return datetime.now().strftime("%Y%m%d.%H%M%S")
