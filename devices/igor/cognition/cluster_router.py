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
import threading
import time
import urllib.request
from typing import Optional

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

# call_types that prefer a batch/heavy model
_BATCH_TYPES = frozenset(["extraction", "batch"])

# ── Health cache ──────────────────────────────────────────────────────────────

_health_cache: dict[str, tuple[bool, float]] = {}  # host → (healthy, timestamp)
_health_lock = threading.Lock()


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

    # If override set, reorder so that machine is first
    if override:
        machines = sorted(machines, key=lambda m: 0 if m.hostname == override else 1)

    for m in machines:
        if m.status != "online":
            continue
        if is_in_use(m.hostname):
            _log.debug("[cluster_router] skipping %s — in use", m.hostname)
            continue
        host = m.ollama_host
        if not _is_ollama_healthy(host):
            _log.debug("[cluster_router] skipping %s — Ollama unreachable", m.hostname)
            continue
        model = m.model_for(call_type)
        _log.debug(
            "[cluster_router] routing %s → %s / %s", call_type, m.hostname, model
        )
        return host, model

    _log.warning("[cluster_router] no local machine available for %s", call_type)
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


# ── Backwards-compat shim for callers that used ClusterRouter singleton ───────


class _RouterShim:
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
