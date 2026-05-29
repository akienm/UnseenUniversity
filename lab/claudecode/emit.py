"""
DEPRECATED — emit.py is a stopgap superseded by DiagnosticBase's JSON sink.
All call sites in pe_chain, ops, scope_guard were removed (2026-05-23).
This file is kept only for the prune_emissions() utility; the emit() function
has no callers and should not be used in new code.

emit.py — Operational log standard: one file per event.

These are rolling operational/diagnostic logs — NOT long-term knowledge.
Retention is 30 days by default. Decisions, goals, memories, and palace
nodes are durable and live elsewhere; these files are the ephemeral raw
event stream for recent debugging and diagnosis.

Filename: YYYYMMDD-HHMMSS-mmm_<source>_<event>[_<key>].json
Location: $IGOR_RUNTIME_ROOT/emissions/<source>/

One emission file per discrete event. grep across the emissions directory
finds every occurrence of any term across all system history within the
retention window, with timestamp + source in the filename.

This is a reasoning surface expansion, not just a log sink: CC can reason
about recent system behavior by sampling the directory, rather than being
limited to what fits in context from a large append-only file.

Pruning: call prune_emissions() from day-close to enforce the 30-day
rolling window. Each ADC device prunes its own local emissions directory.

Usage:
    from lab.claudecode.emit import emit
    emit("pe_chain", "hypothesize_fail", {"ticket_id": t_id, "errors": errs}, key=t_id)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _emissions_root() -> Path:
    base = os.environ.get("IGOR_RUNTIME_ROOT", str(Path.home() / ".unseen_university"))
    return Path(base) / "emissions"


def _clean(s: str, maxlen: int = 40) -> str:
    """Sanitize a string for use as a filename component."""
    return str(s).replace("/", "_").replace(" ", "_").replace(".", "_")[:maxlen]


def emit(
    source: str,
    event: str,
    data: dict,
    key: str | None = None,
) -> Path | None:
    """Write one emission file. Silent noop on any error — never breaks caller.

    Args:
        source: subsystem name (e.g. "pe_chain", "goal_lifecycle", "scope_guard")
        event:  event type (e.g. "hypothesize_fail", "goal_adopt", "escalate_high")
        data:   event-specific fields. ts/source/event are always prepended.
        key:    optional suffix for filename — ticket_id or other discriminator.

    Returns the Path written, or None on error.
    """
    try:
        ts = datetime.now(timezone.utc)
        ts_str = ts.strftime("%Y%m%d-%H%M%S-") + ts.strftime("%f")[:3]
        name_parts = [ts_str, _clean(source), _clean(event)]
        if key:
            name_parts.append(_clean(key, maxlen=30))
        filename = "_".join(name_parts) + ".json"

        out_dir = _emissions_root() / _clean(source)
        out_dir.mkdir(parents=True, exist_ok=True)

        payload: dict = {"ts": ts.isoformat(), "source": source, "event": event}
        payload.update(data)

        out_path = out_dir / filename
        out_path.write_text(
            json.dumps(payload, default=str, indent=2), encoding="utf-8"
        )
        return out_path
    except Exception:
        return None


def prune_emissions(days: int = 30) -> int:
    """Delete emission files older than `days` days. Returns count deleted.

    Call from day-close to enforce the rolling retention window. Each ADC
    device runs this against its own local emissions directory.
    """
    import time

    cutoff = time.time() - days * 86400
    root = _emissions_root()
    if not root.exists():
        return 0
    deleted = 0
    for f in root.rglob("*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                deleted += 1
        except Exception:
            pass
    return deleted
