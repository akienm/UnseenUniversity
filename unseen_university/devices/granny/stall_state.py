"""Granny stall detection and MRU worker ordering.

When Granny has pending work but no wakeable/available worker, she raises a
system alarm, sets a stalled flag, and parks (stops cycling) until manually
resumed. This module manages the stall state and most-recently-used worker
ordering.

Module attributes _STATE and _MRU are monkeypatchable (tests redirect to tmp).
All functions are fail-soft: missing/corrupt files → defaults, no raises.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_STATE = Path.home() / ".granny" / "stall_state.json"
_MRU = Path.home() / ".granny" / "mru.json"
# Per-worker last-dispatch TIMESTAMP (distinct from _MRU, which tracks order not
# time). Feeds the dispatch-health idle-age signal (T-granny-dispatch-observability-gap);
# kept in its own file so the MRU list format the dispatch path reads is untouched.
_LAST_DISPATCH = Path.home() / ".granny" / "last_dispatch.json"


def is_stalled() -> bool:
    """Read stall state; return bool of 'stalled' key. Default False on any error."""
    try:
        data = json.loads(_STATE.read_text(encoding="utf-8"))
        return data.get("stalled", False)
    except (OSError, ValueError):
        return False


def set_stalled(ticket_id: str, reason: str, now=None) -> None:
    """Write stall state: {"stalled": True, "ticket": ticket_id, "reason": reason, "since": <iso>}.

    Creates parent directories; fail-soft (no raise on I/O error, but logs).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        _STATE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "stalled": True,
            "ticket": ticket_id,
            "reason": reason,
            "since": now.isoformat(),
        }
        _STATE.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        log.info("stall_state: stalled for ticket=%s (reason=%s)", ticket_id, reason)
    except Exception as exc:
        log.error("stall_state: set_stalled failed: %s", exc)


def resume() -> None:
    """Delete stall state file (FileNotFoundError-tolerant). Log resume event."""
    try:
        _STATE.unlink()
        log.info("granny: resumed by operator")
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.error("stall_state: resume failed: %s", exc)


def record_dispatch(worker_id: str, now=None) -> None:
    """Record a dispatch to worker_id in the MRU (most-recently-used) list.

    Reads _MRU (JSON list, default []); removes worker_id if present,
    inserts at front (index 0), truncates to 16 entries, writes back.
    Fail-soft: missing/corrupt file → empty list, continue.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        _MRU.parent.mkdir(parents=True, exist_ok=True)
        try:
            mru_list = json.loads(_MRU.read_text(encoding="utf-8"))
            if not isinstance(mru_list, list):
                mru_list = []
        except (OSError, ValueError):
            mru_list = []

        # Remove worker_id if already present, then insert at front
        mru_list = [w for w in mru_list if w != worker_id]
        mru_list.insert(0, worker_id)

        # Truncate to 16 most recent
        mru_list = mru_list[:16]

        _MRU.write_text(json.dumps(mru_list), encoding="utf-8")
        log.debug("stall_state: recorded dispatch to %s", worker_id)
    except Exception as exc:
        log.error("stall_state: record_dispatch failed: %s", exc)


def record_dispatch_time(worker_id: str, now=None) -> None:
    """Record the wall-clock time of a dispatch to worker_id (fail-soft).

    Writes ``{worker_id: <iso>}`` into _LAST_DISPATCH, merging with existing
    entries. Called alongside record_dispatch at the dispatch-success point; a
    pure observability side-effect (never gates a dispatch decision).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        _LAST_DISPATCH.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(_LAST_DISPATCH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        data[worker_id] = now.isoformat()
        _LAST_DISPATCH.write_text(json.dumps(data), encoding="utf-8")
        log.debug("stall_state: recorded dispatch time for %s", worker_id)
    except Exception as exc:
        log.error("stall_state: record_dispatch_time failed: %s", exc)


def last_dispatch_age_s(worker_id: str, now=None) -> float | None:
    """Seconds since worker_id was last dispatched, or None if never recorded.

    Fail-soft: missing/corrupt file or unparseable timestamp → None (treated by
    the summariser as 'not known to be idle', never as idle).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        data = json.loads(_LAST_DISPATCH.read_text(encoding="utf-8"))
        iso = data.get(worker_id)
        if not iso:
            return None
        ts = datetime.fromisoformat(iso)
        return max(0.0, (now - ts).total_seconds())
    except (OSError, ValueError, TypeError):
        return None


def mru_order(candidates: list[str]) -> list[str]:
    """Return candidates sorted by MRU recency (most recent first).

    Reads _MRU list; returns a new list where candidates appearing
    earlier in _MRU come first. Candidates not in _MRU preserve their
    original relative order and sort AFTER the ranked ones.
    Does NOT mutate the input list.
    """
    try:
        mru_list = json.loads(_MRU.read_text(encoding="utf-8"))
        if not isinstance(mru_list, list):
            mru_list = []
    except (OSError, ValueError):
        mru_list = []

    # Build a dict: worker_id → mru_index (or large number if not in list)
    mru_map = {w: i for i, w in enumerate(mru_list)}
    original_indices = {c: i for i, c in enumerate(candidates)}

    # Stable sort: (mru_rank, original_index) where mru_rank is the position
    # in mru_list (lower = more recent) or a large number for non-ranked workers
    def sort_key(candidate):
        mru_rank = mru_map.get(candidate, len(mru_list) + 1000)
        return (mru_rank, original_indices[candidate])

    return sorted(candidates, key=sort_key)
