"""
cluster_router.py — D120: Cluster-aware inference router.

Selects the best (host, model) for each call type based on:
  - Machine health (Ollama /api/tags reachable)
  - CPU load: localhost via os.getloadavg(); remote via /api/tags response latency
  - Active inference count via /api/ps (each loaded+running model = load signal)
  - Time of day (from cloud_mode — daytime favours cloud for training)
  - Manual override: IGOR_INFERENCE_OVERRIDE=<machine_name> env var, or runtime call
  - Call-type capability: which machines can serve which call types

Machine list is auto-derived from env vars — zero new config needed:
  OLLAMA_HOST              → machine "local"     (localhost)
  OLLAMA_REASONING_HOST    → machine "reasoning" (yoga9i), if different from OLLAMA_HOST
  CLUSTER_EXTRA_MACHINES   → optional JSON list of extra {"name","host","models":[...]}

Call types:
  "preparse"   — intent classification; reasoning model preferred
  "ne"         — narrative extraction; any local model acceptable
  "winnow"     — context winnow; any local model acceptable
  "tier2"      — interactive reasoning; reasoning model preferred
  "extraction" — book_learner node extraction; any local model
  "embeddings" — always localhost; never routed elsewhere by this module

Exposes (module-level singleton `router`):
  route(call_type)               → (host_url, model_name) | (None, None) = use cloud
  has_local_capacity(call_type)  → bool
  set_override(machine_name)     → None  (manual: "use yoga9i for everything")
  clear_override()               → None
  status_lines()                 → list[str]  (for /metrics)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

_log = logging.getLogger(__name__)

# ── Call-type → capability categories ────────────────────────────────────────

# reasoning = needs a reasoning-specialised model (DeepSeek-R1 etc.)
# local     = any local model is fine (qwen2.5:7b etc.)
_NEEDS_REASONING = frozenset(["preparse", "tier2"])
_NEEDS_LOCAL = frozenset(["ne", "winnow", "extraction"])

# Load threshold above which a machine is considered saturated
# (normalized load: load_avg_1min / cpu_count)
_LOCAL_SATURATION = float(os.getenv("CLUSTER_SATURATION_THRESHOLD", "0.85"))

# Refresh interval — check health/load this often (seconds)
_REFRESH_TTL = float(os.getenv("CLUSTER_REFRESH_TTL", "30"))

# Remote probe timeout (seconds)
_PROBE_TIMEOUT = float(os.getenv("CLUSTER_PROBE_TIMEOUT", "3"))


# ── Machine spec ──────────────────────────────────────────────────────────────


# ── Network + RAM weights (D211) ─────────────────────────────────────────────

_NETWORK_WEIGHT = {"wired": 1.0, "wifi": 0.7}
_RAM_WEIGHT = {32: 1.0, 16: 0.8, 8: 0.5}

def _ram_weight(ram_gb: int) -> float:
    return _RAM_WEIGHT.get(ram_gb, 0.6)


@dataclass
class MachineInfo:
    name: str
    ollama_host: str
    primary_model: str  # default model for "local" calls
    reasoning_model: str = ""  # reasoning-specialised model (if available)
    is_local: bool = False  # True = can read os.getloadavg()
    hostname: str = ""        # D211: canonical hostname for in_use_now() lookup
    network_type: str = "wifi"  # D211: wired | wifi
    ram_gb: int = 16            # D211: for ram_weight scoring
    is_db_host: bool = False    # D211: db_host role → score penalty

    # Runtime state — updated by _refresh()
    healthy: bool = False
    load_score: float = 0.0  # 0.0=saturated/down, 1.0=idle
    active_models: int = 0  # count from /api/ps
    response_ms: float = 0.0  # /api/tags probe time
    last_checked: float = 0.0

    def model_for(self, call_type: str) -> str:
        if call_type in _NEEDS_REASONING and self.reasoning_model:
            return self.reasoning_model
        return self.primary_model

    def can_serve(self, call_type: str) -> bool:
        if call_type == "embeddings":
            return self.is_local  # embeddings always localhost
        if call_type in _NEEDS_REASONING:
            return bool(self.reasoning_model or self.primary_model)
        return bool(self.primary_model)

    def score(self, call_type: str, override_name: str = "") -> float:
        """
        D211: score = network_weight × (1-load) × ram_weight × db_penalty × capability × override
        Returns 0.0 if unhealthy, can't serve, or currently in-use by a human.
        """
        if not self.healthy or not self.can_serve(call_type):
            return 0.0
        # In-use check (D211) — zero out if human is on this machine
        if self.hostname:
            try:
                from ..tools.routing_tools import in_use_now
                if in_use_now(self.hostname):
                    return 0.0
            except Exception:
                pass
        network_w = _NETWORK_WEIGHT.get(self.network_type, 0.7)
        ram_w = _ram_weight(self.ram_gb)
        db_penalty = 0.2 if self.is_db_host else 1.0
        if call_type in _NEEDS_REASONING and not self.reasoning_model:
            capability_score = 0.5
        else:
            capability_score = 1.0
        override_bonus = 2.0 if self.name == override_name else 1.0
        return self.load_score * network_w * ram_w * db_penalty * capability_score * override_bonus


# ── ClusterRouter ─────────────────────────────────────────────────────────────


class ClusterRouter:
    def __init__(self) -> None:
        self._machines: dict[str, MachineInfo] = {}
        self._override: str = os.getenv("IGOR_INFERENCE_OVERRIDE", "")
        self._lock = threading.Lock()
        self._last_refresh: float = 0.0
        self._built = False

    # ── Machine list ──────────────────────────────────────────────────────────

    def _build_machines(self) -> None:
        """Derive machine list from env vars. Called once on first use."""
        local_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        reasoning_host = os.getenv("OLLAMA_REASONING_HOST", local_host)

        local_model = os.getenv("OLLAMA_LOCAL_MODEL", "qwen2.5:7b")
        reasoning_model = os.getenv("OLLAMA_REASONING_MODEL", "")

        machines: list[MachineInfo] = []

        # Local machine (localhost)
        machines.append(
            MachineInfo(
                name="local",
                ollama_host=local_host,
                primary_model=local_model,
                # If reasoning host IS localhost, it also has the reasoning model
                reasoning_model=reasoning_model if reasoning_host == local_host else "",
                is_local=True,
            )
        )

        # Reasoning machine (yoga9i or other remote) — only if different from localhost
        if reasoning_host and reasoning_host != local_host:
            machines.append(
                MachineInfo(
                    name="reasoning",
                    ollama_host=reasoning_host,
                    primary_model=local_model,  # assume same local model is also available
                    reasoning_model=reasoning_model,
                    is_local=False,
                )
            )

        # Extra machines from CLUSTER_EXTRA_MACHINES env var (JSON)
        extra_json = os.getenv("CLUSTER_EXTRA_MACHINES", "")
        if extra_json:
            try:
                for spec in json.loads(extra_json):
                    machines.append(
                        MachineInfo(
                            name=spec["name"],
                            ollama_host=spec["host"],
                            primary_model=spec.get("primary_model", local_model),
                            reasoning_model=spec.get("reasoning_model", ""),
                            is_local=False,
                        )
                    )
            except Exception as exc:
                _log.warning(
                    f"[cluster_router] CLUSTER_EXTRA_MACHINES parse error: {exc}"
                )

        self._machines = {m.name: m for m in machines}
        self._enrich_from_machines_json()
        self._built = True

    def _enrich_from_machines_json(self) -> None:
        """
        D211: Enrich MachineInfo with network_type, ram_gb, roles from machines.json.
        Matches by IP address (ollama_host contains the IP).
        """
        machines_json = os.path.expanduser("~/.TheIgors/local/machines.json")
        try:
            with open(machines_json) as f:
                data = json.load(f)
        except Exception:
            return

        # Build IP→machine_spec lookup
        ip_map: dict[str, dict] = {}
        for spec in data.get("machines", []):
            ip = spec.get("ip")
            if ip:
                ip_map[ip] = spec

        for m in self._machines.values():
            # Match by IP in ollama_host URL or is_local (localhost)
            spec = None
            if m.is_local:
                # Find the machine with igor_home role or akiendelllinux hostname
                for s in data.get("machines", []):
                    if "igor_home" in s.get("roles", []):
                        spec = s
                        break
            else:
                for ip, s in ip_map.items():
                    if ip in m.ollama_host:
                        spec = s
                        break

            if spec:
                m.hostname = spec.get("hostname", m.hostname)
                m.network_type = spec.get("network_type", "wifi")
                m.ram_gb = spec.get("ram_gb", 16)
                m.is_db_host = "db_host" in spec.get("roles", [])
        _log.info(
            f"[cluster_router] machines: "
            + ", ".join(
                f"{m.name}@{m.ollama_host} (reasoning={bool(m.reasoning_model)})"
                for m in machines
            )
        )

    # ── Health + load probing ─────────────────────────────────────────────────

    def _probe_machine(self, m: MachineInfo) -> None:
        """Update m.healthy, m.load_score, m.response_ms, m.active_models in-place."""
        t0 = time.monotonic()
        try:
            req = urllib.request.Request(
                f"{m.ollama_host}/api/tags",
                headers={"User-Agent": "Igor-ClusterRouter/1.0"},
            )
            with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as resp:
                resp.read()  # drain
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            m.response_ms = elapsed_ms
            m.healthy = True
        except Exception:
            m.healthy = False
            m.load_score = 0.0
            m.last_checked = time.monotonic()
            return

        # /api/ps — count active inferences (loaded models that are running)
        active = 0
        try:
            req_ps = urllib.request.Request(
                f"{m.ollama_host}/api/ps",
                headers={"User-Agent": "Igor-ClusterRouter/1.0"},
            )
            with urllib.request.urlopen(req_ps, timeout=2.0) as resp_ps:
                ps_data = json.loads(resp_ps.read())
            active = len(ps_data.get("models", []))
        except Exception as _bare_e:
            logging.getLogger(__name__).warning("bare except in wild_igor/igor/cognition/cluster_router.py: %s", _bare_e)
        m.active_models = active

        # Load score calculation
        if m.is_local:
            # Localhost: use real CPU load average (Linux/macOS)
            try:
                load1 = os.getloadavg()[0]
                cpu_count = max(os.cpu_count() or 4, 1)
                normalized = load1 / cpu_count
                m.load_score = max(0.0, 1.0 - normalized / _LOCAL_SATURATION)
            except (OSError, AttributeError):
                # Windows: os.getloadavg() not available — use response time proxy
                m.load_score = _response_time_to_score(m.response_ms, active)
        else:
            m.load_score = _response_time_to_score(m.response_ms, active)

        m.last_checked = time.monotonic()

    def _refresh(self, force: bool = False) -> None:
        """Probe all machines if cache is stale. Thread-safe."""
        with self._lock:
            if not self._built:
                self._build_machines()
            now = time.monotonic()
            if not force and (now - self._last_refresh) < _REFRESH_TTL:
                return
            self._last_refresh = now

        # Probe outside lock to avoid blocking callers during I/O
        for m in list(self._machines.values()):
            try:
                self._probe_machine(m)
            except Exception as exc:
                _log.debug(f"[cluster_router] probe error for {m.name}: {exc}")
                m.healthy = False
                m.load_score = 0.0

    # ── Public API ────────────────────────────────────────────────────────────

    def route(self, call_type: str) -> tuple[Optional[str], Optional[str]]:
        """
        Return (host_url, model_name) for the best machine for call_type.
        Returns (None, None) if no local machine is suitable → caller should use cloud.
        """
        self._refresh()
        override = self._override or os.getenv("IGOR_INFERENCE_OVERRIDE", "")

        best: Optional[MachineInfo] = None
        best_score = 0.0

        for m in self._machines.values():
            s = m.score(call_type, override)
            if s > best_score:
                best_score = s
                best = m

        if best is None or best_score <= 0.0:
            return None, None  # → cloud

        return best.ollama_host, best.model_for(call_type)

    def route_batch(self, n: int, call_type: str = "local") -> list[tuple[str, str]]:
        """
        D211: Return up to n (host_url, model) pairs for parallel batch work.
        One per available machine, ranked by score. Used by background/reading workers.
        """
        self._refresh()
        override = self._override or os.getenv("IGOR_INFERENCE_OVERRIDE", "")
        scored = sorted(
            [(m.score(call_type, override), m) for m in self._machines.values()],
            key=lambda x: x[0],
            reverse=True,
        )
        return [
            (m.ollama_host, m.model_for(call_type))
            for s, m in scored[:n]
            if s > 0.0
        ]

    def has_local_capacity(self, call_type: str = "local") -> bool:
        """
        True if at least one local machine is healthy and not saturated for call_type.
        Used by inference_gateway edge conditions.
        """
        self._refresh()
        for m in self._machines.values():
            if m.healthy and m.load_score > 0.1 and m.can_serve(call_type):
                return True
        return False

    def set_override(self, machine_name: str) -> None:
        """Pin all routing to machine_name until clear_override()."""
        _log.info(f"[cluster_router] override set → {machine_name}")
        self._override = machine_name

    def clear_override(self) -> None:
        _log.info("[cluster_router] override cleared")
        self._override = ""

    def force_refresh(self) -> None:
        """Force an immediate probe of all machines (e.g. after config change)."""
        self._refresh(force=True)

    def status_lines(self) -> list[str]:
        """Human-readable status for /metrics."""
        self._refresh()
        lines = []
        override = self._override or os.getenv("IGOR_INFERENCE_OVERRIDE", "")
        if override:
            lines.append(f"  override → {override}")
        for m in self._machines.values():
            state = "✓" if m.healthy else "✗"
            ov = " [OVERRIDE]" if m.name == override else ""
            lines.append(
                f"  {state} {m.name} ({m.ollama_host})  "
                f"load={m.load_score:.2f}  resp={m.response_ms:.0f}ms  "
                f"active_models={m.active_models}  "
                f"primary={m.primary_model}  "
                f"reasoning={m.reasoning_model or '—'}{ov}"
            )
        return lines or ["  (no machines configured)"]


# ── Scoring helper ────────────────────────────────────────────────────────────


def _response_time_to_score(ms: float, active_inferences: int) -> float:
    """
    Convert Ollama /api/tags response time + active model count to a load score.
    0.0 = saturated / down, 1.0 = completely idle.
    """
    # Latency component: 0ms=1.0, 500ms=0.5, 2000ms=0.0
    latency_score = max(0.0, 1.0 - ms / 2000.0)
    # Active inference penalty: each active model reduces score by 0.25 (cap at 1.0)
    inference_penalty = min(active_inferences * 0.25, 1.0)
    return latency_score * (1.0 - inference_penalty)


# ── Module-level singleton ────────────────────────────────────────────────────

router = ClusterRouter()
