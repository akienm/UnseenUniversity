"""
cluster_router.py — Simple inference router (#342).

Decision:
  Walk machines by inference_rank (from DB via machine_manager).
  Skip in-use (in_use_hours window or override) and unhealthy (Ollama down).
  Return first suitable (host_url, model).
  Return (None, None) only if every machine — including akiendelllinux — is unreachable.

Callers treat (None, None) as "use cloud" via the tier ladder.

Machine config lives in the `machines` DB table (machine_manager.py).
Ollama health is probed per-call with a 30s TTL cache.
"""

from __future__ import annotations

import logging
import os
import statistics
import threading
import time
import urllib.request
from collections import deque
from dataclasses import dataclass
from typing import Optional

from ..igor_base import IgorBase
from .machine_manager import (
    MachineRecord,
    get_ranked_machines,
    is_in_use,
)

_log = logging.getLogger(__name__)

# Ollama probe timeout
_PROBE_TIMEOUT = float(os.getenv("CLUSTER_PROBE_TIMEOUT", "3"))

# Health cache TTL — don't hammer Ollama endpoints
_HEALTH_TTL = float(os.getenv("CLUSTER_REFRESH_TTL", "30"))

# 2026-04-18: two-column (light/batch) local-model scheme collapsed to a
# single model per machine. _BATCH_TYPES was the route-to-batch-model
# switch; it's no longer consulted. model_for() always returns ollama_model.
# Kept as an empty set for any stragglers still importing the name.
_BATCH_TYPES: frozenset = frozenset()

# ── Health cache ──────────────────────────────────────────────────────────────

_health_cache: dict[str, tuple[bool, float]] = {}  # host → (healthy, timestamp)
_health_lock = threading.Lock()

# ── No-machine warning rate limiter ──────────────────────────────────────────
# When a batch of N items all hit route() simultaneously, we'd get N warnings.
# Emit one WARNING per call_type per 60s; rest are DEBUG.
_no_machine_warn: dict[str, float] = {}  # call_type → last warn timestamp
_no_machine_warn_lock = threading.Lock()
_NO_MACHINE_WARN_INTERVAL = 60.0


def _is_ollama_healthy(host_url: str) -> bool:
    """Probe Ollama /api/tags with TTL cache. Returns True if reachable."""
    with _health_lock:
        cached = _health_cache.get(host_url)
        if cached and (time.monotonic() - cached[1]) < _HEALTH_TTL:
            return cached[0]

    healthy = False
    try:
        req = urllib.request.Request(
            f"{host_url}/api/tags",
            headers={"User-Agent": "Igor-ClusterRouter/2.0"},
        )
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as resp:
            healthy = resp.status == 200
    except Exception:
        healthy = False

    with _health_lock:
        _health_cache[host_url] = (healthy, time.monotonic())

    return healthy


def _active_inferences(host_url: str) -> int:
    """Count active Ollama inferences via /api/ps. Returns 0 on error."""
    try:
        req = urllib.request.Request(
            f"{host_url}/api/ps",
            headers={"User-Agent": "Igor-ClusterRouter/2.0"},
        )
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            import json

            return len(json.loads(resp.read()).get("models", []))
    except Exception:
        return 0


# ── Public API ────────────────────────────────────────────────────────────────


def route(call_type: str) -> tuple[Optional[str], Optional[str]]:
    """
    Return (host_url, model_name) for the best available local machine.
    Returns (None, None) if no local machine is reachable → caller uses cloud.
    """
    override = os.getenv("IGOR_INFERENCE_OVERRIDE", "")
    machines = get_ranked_machines()

    # Diagnostic: why are we here?
    if not machines:
        _log.warning(
            "[cluster_router] ROUTE_FAIL: no machines in DB (empty result from machine_manager)"
        )
        return None, None

    # If override set, reorder so that machine is first
    if override:
        machines = sorted(machines, key=lambda m: 0 if m.hostname == override else 1)

    skip_reasons = {}  # hostname → reason
    for m in machines:
        if m.status != "online":
            skip_reasons[m.hostname] = f"status={m.status}"
            continue
        if is_in_use(m.hostname):
            skip_reasons[m.hostname] = "in_use_override|hours_window"
            _log.debug("[cluster_router] skipping %s — in use", m.hostname)
            continue
        host = m.ollama_host
        if not _is_ollama_healthy(host):
            skip_reasons[m.hostname] = f"ollama_unhealthy@{host}"
            _log.debug(
                "[cluster_router] skipping %s — Ollama unreachable @ %s",
                m.hostname,
                host,
            )
            continue
        model = m.model_for(call_type)
        _log.debug(
            "[cluster_router] routing %s → %s / %s", call_type, m.hostname, model
        )
        return host, model

    # All machines filtered out — log diagnostic detail
    now = time.monotonic()
    with _no_machine_warn_lock:
        last = _no_machine_warn.get(call_type, 0.0)
        if now - last >= _NO_MACHINE_WARN_INTERVAL:
            skip_summary = " | ".join(
                f"{hostname}:{reason}" for hostname, reason in skip_reasons.items()
            )
            _log.warning(
                "[cluster_router] ROUTE_FAIL: no local machine available for %s — all skipped: %s",
                call_type,
                skip_summary,
            )
            _no_machine_warn[call_type] = now
        else:
            _log.debug(
                "[cluster_router] no local machine available for %s (suppressed — next warn in %.0fs)",
                call_type,
                _NO_MACHINE_WARN_INTERVAL - (now - last),
            )
    return None, None


def route_batch(n: int, call_type: str = "extraction") -> list[tuple[str, str]]:
    """
    Return up to n (host_url, model) pairs for parallel batch work.
    Used by background/reading workers.
    """
    machines = get_ranked_machines()
    results = []
    for m in machines:
        if len(results) >= n:
            break
        if m.status != "online" or is_in_use(m.hostname):
            continue
        host = m.ollama_host
        if _is_ollama_healthy(host):
            results.append((host, m.model_for(call_type)))
    return results


def has_local_capacity(call_type: str = "local") -> bool:
    """True if at least one local machine is healthy and not in-use."""
    for m in get_ranked_machines():
        if m.status != "online" or is_in_use(m.hostname):
            continue
        if _is_ollama_healthy(m.ollama_host):
            return True
    return False


def force_refresh() -> None:
    """Invalidate health cache — force re-probe on next route() call."""
    with _health_lock:
        _health_cache.clear()


def set_override(machine_name: str) -> None:
    """Pin routing to machine_name (env var approach for process lifetime)."""
    os.environ["IGOR_INFERENCE_OVERRIDE"] = machine_name
    _log.info("[cluster_router] override set → %s", machine_name)


def clear_override() -> None:
    os.environ.pop("IGOR_INFERENCE_OVERRIDE", None)
    _log.info("[cluster_router] override cleared")


def status_lines() -> list[str]:
    """Human-readable status for /metrics."""
    override = os.getenv("IGOR_INFERENCE_OVERRIDE", "")
    lines = []
    if override:
        lines.append(f"  override → {override}")
    for m in get_ranked_machines():
        in_use = is_in_use(m.hostname)
        host = m.ollama_host
        with _health_lock:
            cached = _health_cache.get(host)
        healthy = cached[0] if cached else "?"
        ov = " [OVERRIDE]" if m.hostname == override else ""
        state = (
            "IN-USE"
            if in_use
            else ("up" if healthy is True else ("down" if healthy is False else "?"))
        )
        lines.append(
            f"  rank={m.inference_rank} {m.hostname:20s} {state:8s} "
            f"{m.network_type:5s} {m.ram_gb}GB  model={m.ollama_model}{ov}"
        )
    return lines or ["  (no machines in DB)"]


# ── Capacity profiling (T-cluster-router-capacity-profile) ───────────────────
#
# Per-machine sliding-window stats layer for distributed preparse routing.
# cluster_router already tracks machine health, in-use detection, and
# ranked scoring. It did NOT track per-machine latency-by-input-size or
# learn a "safe input ceiling" for each machine. T-preparse-router
# consumes this layer to decide "yogai7 handles ≤3 sentences, yoga9i
# handles ≤7" when grouping atomic chunks into batches.
#
# Storage: in-memory sliding window of last _MAX_WINDOW observations per
# machine. Persistence to DB is a follow-on; in-process stats are enough
# for per-caller routing decisions. No cross-machine gossip yet.

_MAX_WINDOW = 50
_COLD_START_SILENCE_SEC = 300.0  # 5 min — machine reboot/model-reload threshold
_OVERLOAD_MULT = 1.5  # last-5 p50 ≥ this * window p50 → overloaded
_OVERLOAD_MIN_OBS = 5  # min observations before overload check fires
_SAFE_CEILING_SUCCESS_THRESHOLD = 0.95
_SAFE_CEILING_MIN_OBS_PER_BUCKET = 3  # need this many obs in bucket before trusting it
_DEFAULT_CEILING_WHEN_UNKNOWN = 150  # conservative ceiling for unseen machines

# Size buckets (input_tokens) for per-bucket latency and ceiling queries.
# Upper bound of the last bucket is effectively unbounded.
_SIZE_BUCKETS: tuple[tuple[int, int], ...] = (
    (0, 50),
    (51, 150),
    (151, 500),
    (501, 2000),
    (2001, 100000),
)


@dataclass(frozen=True)
class CapacityObs:
    """One recorded dispatch outcome for the capacity profile.

    ts: time.monotonic() at record time — used for cold-start detection
    input_tokens: token count of the dispatched input
    latency_ms: observed round-trip latency in milliseconds
    outcome: "success" | "timeout" | "error"
    """

    ts: float
    input_tokens: int
    latency_ms: int
    outcome: str


class _CapacityProfile:
    """Sliding-window per-machine stats. Thread-safe."""

    def __init__(self, max_window: int = _MAX_WINDOW) -> None:
        self._state: dict[str, deque[CapacityObs]] = {}
        self._lock = threading.Lock()
        self._max_window = max_window

    def record(
        self, machine: str, input_tokens: int, latency_ms: int, outcome: str
    ) -> None:
        """Append an observation to this machine's sliding window."""
        obs = CapacityObs(
            ts=time.monotonic(),
            input_tokens=max(0, int(input_tokens)),
            latency_ms=max(0, int(latency_ms)),
            outcome=outcome if outcome in ("success", "timeout", "error") else "error",
        )
        with self._lock:
            dq = self._state.setdefault(machine, deque(maxlen=self._max_window))
            dq.append(obs)

    def _bucket_for(self, input_tokens: int) -> tuple[int, int] | None:
        for lo, hi in _SIZE_BUCKETS:
            if lo <= input_tokens <= hi:
                return (lo, hi)
        return None

    def safe_ceiling(self, machine: str) -> int:
        """Largest input-token bucket where success_rate ≥ threshold in
        the last-10 observations for that bucket. If no data, return a
        conservative default so the machine is usable at small sizes."""
        with self._lock:
            dq = self._state.get(machine)
            if not dq:
                return _DEFAULT_CEILING_WHEN_UNKNOWN
            obs_list = list(dq)
        # Walk buckets largest → smallest, pick the first that qualifies.
        for lo, hi in reversed(_SIZE_BUCKETS):
            in_bucket = [o for o in obs_list if lo <= o.input_tokens <= hi]
            if len(in_bucket) < _SAFE_CEILING_MIN_OBS_PER_BUCKET:
                continue
            recent = in_bucket[-10:]
            ok = sum(1 for o in recent if o.outcome == "success")
            rate = ok / len(recent)
            if rate >= _SAFE_CEILING_SUCCESS_THRESHOLD:
                return hi
        # No bucket qualifies → conservative default
        return _DEFAULT_CEILING_WHEN_UNKNOWN

    def p50_latency(
        self, machine: str, bucket: tuple[int, int] | None = None
    ) -> float | None:
        """Median latency for observations in `bucket` (or all obs if None).
        Returns None if no observations."""
        with self._lock:
            dq = self._state.get(machine)
            if not dq:
                return None
            if bucket is None:
                samples = [o.latency_ms for o in dq]
            else:
                lo, hi = bucket
                samples = [o.latency_ms for o in dq if lo <= o.input_tokens <= hi]
        if not samples:
            return None
        return float(statistics.median(samples))

    def is_overloaded(self, machine: str) -> bool:
        """True if last-5 p50 latency has risen ≥ OVERLOAD_MULT × window p50."""
        with self._lock:
            dq = self._state.get(machine)
            if not dq or len(dq) < _OVERLOAD_MIN_OBS:
                return False
            obs_list = list(dq)
        recent = obs_list[-5:]
        recent_p50 = statistics.median(o.latency_ms for o in recent)
        window_p50 = statistics.median(o.latency_ms for o in obs_list)
        if window_p50 <= 0:
            return False
        return recent_p50 >= _OVERLOAD_MULT * window_p50

    def is_cold_start(self, machine: str) -> bool:
        """True if most recent observation is > SILENCE_SEC ago and the
        window has prior observations. Callers should weight subsequent
        observations more heavily until the window refills."""
        with self._lock:
            dq = self._state.get(machine)
            if not dq or len(dq) < 2:
                return False
            last = dq[-1]
        return (time.monotonic() - last.ts) > _COLD_START_SILENCE_SEC

    def observations(self, machine: str) -> list[CapacityObs]:
        """Copy of the current window for introspection. Test/debug helper."""
        with self._lock:
            dq = self._state.get(machine)
            return list(dq) if dq else []

    def clear(self) -> None:
        """Wipe all state. Test helper."""
        with self._lock:
            self._state.clear()


_capacity = _CapacityProfile()


def record_dispatch(
    machine: str, input_tokens: int, latency_ms: int, outcome: str
) -> None:
    """Module-level API: record a dispatch outcome for capacity profiling."""
    _capacity.record(machine, input_tokens, latency_ms, outcome)


def safe_ceiling(machine: str) -> int:
    """Module-level API: largest safe input size for this machine."""
    return _capacity.safe_ceiling(machine)


def p50_latency(machine: str, bucket: tuple[int, int] | None = None) -> float | None:
    """Module-level API: median latency (overall or by bucket)."""
    return _capacity.p50_latency(machine, bucket)


def is_overloaded(machine: str) -> bool:
    """Module-level API: load-trend check for this machine."""
    return _capacity.is_overloaded(machine)


def is_cold_start(machine: str) -> bool:
    """Module-level API: did this machine go silent since last obs?"""
    return _capacity.is_cold_start(machine)


def capacity_observations(machine: str) -> list[CapacityObs]:
    """Module-level API: current window snapshot for introspection."""
    return _capacity.observations(machine)


def capacity_clear() -> None:
    """Module-level API: wipe profile state (test helper)."""
    _capacity.clear()


# ── Backwards-compat shim for callers that used ClusterRouter singleton ───────


class _RouterShim(IgorBase):
    """Thin shim so old `from .cluster_router import router` calls still work."""

    def route(self, call_type: str) -> tuple[Optional[str], Optional[str]]:
        return route(call_type)

    def route_batch(
        self, n: int, call_type: str = "extraction"
    ) -> list[tuple[str, str]]:
        return route_batch(n, call_type)

    def has_local_capacity(self, call_type: str = "local") -> bool:
        return has_local_capacity(call_type)

    def set_override(self, name: str) -> None:
        set_override(name)

    def clear_override(self) -> None:
        clear_override()

    def force_refresh(self) -> None:
        force_refresh()

    def status_lines(self) -> list[str]:
        return status_lines()


router = _RouterShim()
