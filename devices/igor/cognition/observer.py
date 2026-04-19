import logging

"""
observer.py — lightweight self-instrumentation primitive (Part D).

Two layers:
    metrics.log      — static forensic log (us debugging Igor)
    EXPERIENTIAL DB  — dynamic self-observation (Igor debugging himself)

Igor places observe() calls wherever he notices friction or wants visibility.
When self-edit is re-enabled, Igor can add new call-sites himself.

The habit compiler can eventually notice patterns in EXPERIENTIAL memories:
    "confluence_get_page consistently > 6s" → routing preference
    "tier.3 selected but actual_latency > budget" → recalibrate estimate

Never raises. Non-blocking. Swallows all exceptions.
"""

import inspect
from datetime import datetime
from pathlib import Path

from ..paths import paths

_cortex = None  # wired in at boot via observer.init()

_LOG_DIR = paths().logs
_LOG_FILE = "metrics.log"
_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def init(cortex) -> None:
    """Call once at boot to wire in the cortex reference for DB writes."""
    global _cortex
    _cortex = cortex


def observe(label: str, value, context: dict = None) -> None:
    """
    Record a metric observation.

    label:   short identifier — e.g. "tier_selected", "tool_latency"
    value:   the observed value (any type; stored as str)
    context: optional key/value dict of related values

    Writes to:
        1. ~/.TheIgors/logs/metrics.log   (forensic layer — always on)
        2. cortex EXPERIENTIAL memory     (self-observation — if cortex wired in)
    """
    try:
        frame = inspect.stack()[1]
        caller = Path(frame.filename).stem
    except Exception:
        caller = "unknown"

    ctx_str = "|".join(f"{k}={v}" for k, v in (context or {}).items())
    ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    entry = f"{ts}|observe|{label}|{value}|caller={caller}"
    if ctx_str:
        entry += f"|{ctx_str}"

    # Layer 1: forensic log
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = _LOG_DIR / _LOG_FILE
        existing = ""
        if path.exists():
            if path.stat().st_size > _MAX_BYTES:
                old = path.with_suffix(".old")
                if old.exists():
                    old.unlink()
                path.rename(old)
            else:
                existing = path.read_text(encoding="utf-8")
        path.write_text(entry + "\n" + existing, encoding="utf-8")
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/cognition/observer.py: %s", _bare_e
        )

    # Layer 2: EXPERIENTIAL memory
    if _cortex is not None:
        try:
            from ..memory.models import Memory, MemoryType

            narrative = f"METRIC|{label}|{value}"
            if ctx_str:
                narrative += f"|{ctx_str[:200]}"
            # T-provenance-gap-metric-memories: observer.observe is the
            # canonical 'record a METRIC|*' deposit site. Stamp
            # 'runtime:observer' so the provenance gate sees where these
            # came from and doesn't log PROVENANCE_GAP. label goes into
            # metadata.observer_label for audit queries.
            m = Memory(
                narrative=narrative,
                memory_type=MemoryType.EXPERIENTIAL,
                valence=0.0,
                source="runtime:observer",
                metadata={
                    "label": label,
                    "value": str(value),
                    "caller": caller,
                    "observer_label": label,
                    "deposited_by": "runtime:observer",
                    **(context or {}),
                },
            )
            _cortex.store(m)
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/cognition/observer.py: %s", _bare_e
            )
