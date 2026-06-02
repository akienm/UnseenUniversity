"""
dispatch_config.py — Config reader and gate evaluator for Granny worker dispatch.

Reads config/profiles/granny.yaml and evaluates per-worker dispatch gates.
Gate evaluation is a pure function: all I/O is injected via context or
the semaphore_fn parameter so tests can run without filesystem/DB side effects.

Gate types supported:
  time_window       — HH:MM-HH:MM local time; overnight ranges (21:00-06:00) work
  away_semaphore    — semaphore name (e.g. CC.0.available.true); calls is_available
  available_semaphore — same protocol
  usage_max_pct     — float; True when usage_pct in context is below this value
  max_concurrent    — int; True when cc0_busy in context is False (for max=1)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

log = logging.getLogger(__name__)

_UU_ROOT = Path(__file__).parent.parent.parent.resolve()
_CONFIG_PATH = Path(
    os.environ.get(
        "GRANNY_CONFIG_PATH", str(_UU_ROOT / "config" / "profiles" / "granny.yaml")
    )
)


def load_dispatch_config() -> dict:
    """Load workers config from granny.yaml. Returns {'workers': {}} on any error."""
    for path in (_CONFIG_PATH, Path.home() / ".granny" / "granny.yaml"):
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text()) or {}
                log.debug("dispatch_config: loaded from %s", path)
                return data
            except Exception as e:
                log.warning("dispatch_config: failed to load %s: %s", path, e)
    log.warning("dispatch_config: no granny.yaml found — using empty config")
    return {"workers": {}}


def _parse_time_window(spec: str) -> tuple[int, int, int, int]:
    """Parse 'HH:MM-HH:MM' into (start_h, start_m, end_h, end_m)."""
    start, end = spec.split("-", 1)
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    return sh, sm, eh, em


def _in_time_window(spec: str, now: datetime) -> bool:
    """Return True when now falls within the HH:MM-HH:MM window (handles overnight)."""
    try:
        sh, sm, eh, em = _parse_time_window(spec)
        cur = (now.hour, now.minute)
        start = (sh, sm)
        end = (eh, em)
        if start <= end:
            # Same-day window: e.g. 09:00-17:00
            return start <= cur < end
        else:
            # Overnight window: e.g. 21:00-06:00
            return cur >= start or cur < end
    except Exception as e:
        log.warning("dispatch_config: bad time_window %r: %s — allowing", spec, e)
        return True


def _semaphore_worker_id(semaphore_spec: str) -> str:
    """Extract worker_id from a semaphore spec like 'CC.0.available.true'."""
    for suffix in (".available.true", ".available.false", ".available"):
        if semaphore_spec.endswith(suffix):
            return semaphore_spec[: -len(suffix)]
    return semaphore_spec


def evaluate_gate(
    gate_name: str,
    gate_value: Any,
    context: dict,
    semaphore_fn: Callable[[str], bool],
) -> bool:
    """Evaluate one gate. Returns True when the gate passes (dispatch allowed).

    context keys used:
      now         — datetime (defaults to datetime.now())
      usage_pct   — float CC 5h utilization (defaults to 0.0 = always ok)
      cc0_busy    — bool (True = CC.0 already has in_progress ticket)
    """
    if gate_value is None:
        return True

    if gate_name == "time_window":
        now = context.get("now") or datetime.now()
        return _in_time_window(str(gate_value), now)

    if gate_name in ("away_semaphore", "available_semaphore"):
        worker_id = _semaphore_worker_id(str(gate_value))
        return semaphore_fn(worker_id)

    if gate_name == "usage_max_pct":
        pct = context.get("usage_pct", 0.0)
        return float(pct) < float(gate_value)

    if gate_name == "max_concurrent":
        busy = context.get("cc0_busy", False)
        return not busy

    log.debug("dispatch_config: unknown gate %r — allowing", gate_name)
    return True


def evaluate_worker_gates(
    worker_id: str,
    worker_config: dict,
    context: dict,
    *,
    semaphore_fn: Optional[Callable[[str], bool]] = None,
) -> bool:
    """Return True when all gates for this worker pass.

    semaphore_fn: injectable for testing (defaults to is_available from availability.py).
    """
    if semaphore_fn is None:
        from devices.granny.availability import is_available

        semaphore_fn = is_available

    gates = worker_config.get("gates") or {}
    for name, value in gates.items():
        if not evaluate_gate(name, value, context, semaphore_fn):
            log.debug(
                "dispatch_config: worker %s gate %r=%r failed", worker_id, name, value
            )
            return False
    return True


def get_worker_config(worker_id: str, config: Optional[dict] = None) -> Optional[dict]:
    """Return the config dict for a specific worker, or None if not defined."""
    if config is None:
        config = load_dispatch_config()
    return (config.get("workers") or {}).get(worker_id)
