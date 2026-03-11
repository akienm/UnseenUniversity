"""
LocalPool / BatchPool — Ollama-backed local inference pools.

All local inference runs on Ollama. Igor can self-manage models via
`ollama pull <model>` / `ollama list` without manual server restarts.

Config env vars:
  OLLAMA_LOCAL_MODEL     — interactive 1B model  (default: llama3.2:1b)
  OLLAMA_BATCH_MODEL     — batch / document model (default: qwen2.5:14b)
  OLLAMA_BATCH_HOST      — override batch Ollama host (default: auto from machines.json)
  LATENCY_BUDGET_SECONDS — budget before escalating to cloud (default: 8.0)
  ROUTING_WEIGHT_COST    — cost weight [0.2-0.8] (default: 0.60)
  ROUTING_WEIGHT_SPEED   — speed weight [0.2-0.8] (default: 0.40)
  BATCH_SLOW_THRESHOLD_SECS — avg duration that triggers OR fallback (default: 3600)
  BATCH_OR_MODEL         — OpenRouter fallback for batch (default: qwen/qwen2.5-14b-instruct)

LocalPool:    round-robin Ollama instances across the cluster for interactive turns.
BatchPool:    single best background/batch Ollama machine for document work.
              Falls back to OpenRouter (qwen2.5-14b) when local is slow or unavailable.

Backwards-compat aliases: LocalKoboldPool, BatchKoboldPool.
"""

from __future__ import annotations

import json
import os
import platform
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .reasoners.ollama_reasoner import OllamaReasoner, OLLAMA_LOCAL_MODEL, OLLAMA_HOST, is_healthy
from ..memory.models import Memory

MACHINES_JSON       = Path.home() / ".TheIgors" / "local" / "machines.json"
BENCHMARK_DIR       = Path.home() / ".TheIgors" / "benchmarks"
OLLAMA_PORT         = 11434

OLLAMA_BATCH_MODEL      = os.getenv("OLLAMA_BATCH_MODEL", "qwen2.5:14b")
BATCH_TTL_HOURS         = 24
_DEFAULT_LATENCY_BUDGET = 8.0

# Lower number = higher priority; realtime machines tried first
PRIORITY_ORDER: dict[str, int] = {
    "priority.realtime":    0,
    "priority.main_loop":   1,
    "priority.background":  2,
    "priority.batch":       3,
}

# ── Machine helpers ────────────────────────────────────────────────────────────

def _parse_online_machines() -> list[dict]:
    """Parse machines.json; return online machines sorted by priority."""
    if not MACHINES_JSON.exists():
        return []
    try:
        data = json.loads(MACHINES_JSON.read_text(encoding="utf-8"))
        machines = [
            m for m in data.get("machines", [])
            if m.get("ip") and m.get("status", "online") != "offline"
        ]
        machines.sort(key=lambda m: (
            PRIORITY_ORDER.get(m.get("priority", ""), 99),
            0 if m.get("network", "").lower() == "wired" else 1,
        ))
        return machines
    except Exception:
        return []


def parse_capabilities(row: dict) -> list[str]:
    caps = row.get("capabilities", [])
    if isinstance(caps, list):
        return caps
    return [c.strip() for c in str(caps).split(",") if c.strip()]


# ── Benchmarking ───────────────────────────────────────────────────────────────

def _benchmark_path(hostname: str) -> Path:
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    return BENCHMARK_DIR / f"{hostname}.json"


def _load_benchmark_cache(hostname: str, model: str) -> dict | None:
    path = _benchmark_path(hostname)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("model") != model:
            return None
        ts = datetime.fromisoformat(data["measured_at"])
        if (datetime.now() - ts).total_seconds() / 3600 > BATCH_TTL_HOURS:
            return None
        return data
    except Exception:
        return None


def _run_benchmark(model: str, host: str | None = None) -> dict:
    import ollama as _ollama
    client = _ollama.Client(host=host) if host else _ollama
    _BENCH_PROMPTS = [
        "Reply with only the word 'ready'.",
        "What is 2+2? Reply with only the number.",
        "True or false: water is wet. Reply with one word.",
    ]
    timings: list[float] = []
    total_tokens = 0
    for prompt in _BENCH_PROMPTS:
        t0 = time.perf_counter()
        response = client.chat(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            options={"temperature": 0.0, "num_predict": 20},
        )
        elapsed = time.perf_counter() - t0
        tokens_out = (
            getattr(response, "eval_count", None)
            or (response.get("eval_count") if isinstance(response, dict) else None)
            or 1
        )
        timings.append(elapsed)
        total_tokens += tokens_out
    avg_latency_ms = (sum(timings) / len(timings)) * 1000
    tokens_per_sec = total_tokens / sum(timings) if sum(timings) > 0 else 1.0
    return {
        "model":          model,
        "tokens_per_sec": round(tokens_per_sec, 1),
        "avg_latency_ms": round(avg_latency_ms, 1),
        "measured_at":    datetime.now().isoformat(),
    }


def _save_benchmark_cache(hostname: str, result: dict) -> None:
    try:
        _benchmark_path(hostname).write_text(json.dumps(result, indent=2), encoding="utf-8")
    except Exception:
        pass


def _in_benchmark_window() -> bool:
    hour = datetime.now().hour
    start = int(os.getenv("IGOR_BENCHMARK_WINDOW_START", "22"))
    end   = int(os.getenv("IGOR_BENCHMARK_WINDOW_END",   "6"))
    if start > end:
        return hour >= start or hour < end
    return start <= hour < end


def _init_benchmark_async(model: str, on_complete) -> None:
    hostname = platform.node()
    cached = _load_benchmark_cache(hostname, model)
    if cached:
        on_complete(cached)
        return
    if not _in_benchmark_window():
        return

    def _worker():
        try:
            result = _run_benchmark(model)
            _save_benchmark_cache(hostname, result)
            on_complete(result)
            from rich.console import Console
            Console().print(
                f"[dim][BENCH] {model} @ {result['tokens_per_sec']} tok/sec "
                f"on {hostname} — latency budget active[/]"
            )
        except Exception as e:
            from rich.console import Console
            Console().print(f"[dim][BENCH] benchmark failed for {model} ({e})[/]")

    threading.Thread(target=_worker, daemon=True, name="ollama-benchmark").start()


# ── RoutingWeights ─────────────────────────────────────────────────────────────

class RoutingWeights:
    CLAMP_MIN = 0.2
    CLAMP_MAX = 0.8
    STEP      = 0.05

    def __init__(self):
        cost_w  = float(os.getenv("ROUTING_WEIGHT_COST",  "0.60"))
        speed_w = float(os.getenv("ROUTING_WEIGHT_SPEED", "0.40"))
        self.cost_weight  = max(self.CLAMP_MIN, min(self.CLAMP_MAX, cost_w))
        self.speed_weight = max(self.CLAMP_MIN, min(self.CLAMP_MAX, speed_w))

    def adjust(self, signal: str) -> None:
        if signal == "speed_pressure":
            self.speed_weight = min(self.CLAMP_MAX, self.speed_weight + self.STEP)
            self.cost_weight  = max(self.CLAMP_MIN, self.cost_weight  - self.STEP)
        elif signal == "cost_pressure":
            self.cost_weight  = min(self.CLAMP_MAX, self.cost_weight  + self.STEP)
            self.speed_weight = max(self.CLAMP_MIN, self.speed_weight - self.STEP)

    def score_local(self, est_latency: float, budget: float) -> tuple[float, float, float]:
        cost_score  = 1.0
        speed_score = max(0.0, 1.0 - (est_latency / budget)) if budget > 0 else 0.0
        tier_score  = (cost_score * self.cost_weight) + (speed_score * self.speed_weight)
        return cost_score, speed_score, tier_score

    def __repr__(self) -> str:
        return f"weights(cost={self.cost_weight:.2f},speed={self.speed_weight:.2f})"


# ── LocalPool ──────────────────────────────────────────────────────────────────

BENCHMARK_DIR = Path.home() / ".TheIgors" / "benchmarks"

class LocalPool:
    """
    Round-robin pool of OllamaReasoner instances across the cluster.
    Used for interactive tier.2 reasoning. Igor can switch models with /model.
    """

    def __init__(self):
        self._reasoners:  list[OllamaReasoner] = []
        self._index       = 0
        self._benchmark:  dict | None = None
        self.weights      = RoutingWeights()
        self.model        = OLLAMA_LOCAL_MODEL
        self._refresh()
        self._benchmark = {"tokens_per_sec": 1.0, "avg_latency_ms": 1000}

    def _on_benchmark_done(self, result: dict) -> None:
        self._benchmark = result

    def _refresh(self):
        """Rebuild OllamaReasoner list from machines.json."""
        machines = _parse_online_machines()
        reasoners = []
        for m in machines:
            ip = m.get("ip", "")
            if not ip:
                continue
            host = f"http://{ip}:{OLLAMA_PORT}"
            reasoners.append(OllamaReasoner(model=self.model, host=host))
        # Always include local Ollama
        reasoners.append(OllamaReasoner(model=self.model, host=OLLAMA_HOST))
        self._reasoners = reasoners
        self._index     = 0

    def record_benchmark(self, hostname: str, task: str, latency: float) -> None:
        BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
        bench_file = BENCHMARK_DIR / f"{hostname}.json"
        try:
            data = json.loads(bench_file.read_text()) if bench_file.exists() else {}
        except Exception:
            data = {}
        if task not in data:
            data[task] = {}
        data[task].update({
            "last_latency": latency,
            "last_update":  datetime.now().isoformat(),
            "avg": (data[task].get("avg", latency) + latency) / 2,
        })
        bench_file.write_text(json.dumps(data, indent=2))

    def _next_reasoner(self) -> Iterator[OllamaReasoner]:
        n = len(self._reasoners)
        for i in range(n):
            yield self._reasoners[(self._index + i) % n]

    def _estimate_latency(self, user_input: str) -> float | None:
        if self._benchmark is None:
            return None
        tps = self._benchmark.get("tokens_per_sec", 0)
        if tps <= 0:
            return None
        return max(1, len(user_input) // 4) / tps

    def reason(
        self,
        user_input: str,
        relevant_memories: list[Memory],
        core_patterns: list[Memory],
        instance_id: str,
        preparse_csb: str = "",
        force_local: bool = False,
    ) -> tuple[str, float]:
        from .forensic_logger import log_routing_decision

        budget      = float(os.getenv("LATENCY_BUDGET_SECONDS", str(_DEFAULT_LATENCY_BUDGET)))
        est_latency = self._estimate_latency(user_input)

        cost_score = speed_score = tier_score = 0.0
        if est_latency is not None and not force_local:
            cost_score, speed_score, tier_score = self.weights.score_local(est_latency, budget)
            if est_latency > budget:
                log_routing_decision(
                    est_latency_s=est_latency, budget_s=budget, tier_selected="tier.2",
                    cost_score=cost_score, speed_score=speed_score, tier_score=tier_score,
                    escalated=True, weights=repr(self.weights), proc_id="PROC_ROUTING_ESCALATE",
                )
                raise RuntimeError(
                    f"Local too slow: est {est_latency:.1f}s > budget {budget}s "
                    f"(speed_score={speed_score:.2f})"
                )

        t0 = time.perf_counter()
        last_exc = None
        for reasoner in self._next_reasoner():
            try:
                result         = reasoner.reason(user_input, relevant_memories, core_patterns, instance_id)
                actual_latency = time.perf_counter() - t0
                self._index    = (self._index + 1) % len(self._reasoners)
                log_routing_decision(
                    est_latency_s=est_latency, actual_latency_s=actual_latency,
                    budget_s=budget, tier_selected="tier.2",
                    cost_score=cost_score, speed_score=speed_score, tier_score=tier_score,
                    escalated=False, weights=repr(self.weights), proc_id="PROC_ROUTING_LOCAL",
                )
                return result
            except Exception as exc:
                last_exc = exc
                continue

        raise RuntimeError(
            f"All {len(self._reasoners)} Ollama instances failed. Last error: {last_exc}"
        )

    def set_model(self, model: str) -> str:
        if model != self.model:
            self.model      = model
            self._benchmark = None
            _init_benchmark_async(model, self._on_benchmark_done)
        for r in self._reasoners:
            r.model = model
        return model

    def machines_summary(self) -> str:
        machines = _parse_online_machines()
        parts = [
            f"{m['hostname']}({m.get('priority','?').replace('priority.','')})"
            for m in machines
        ]
        parts.append("localhost(fallback)")
        if self._benchmark:
            parts.append(f"bench={self._benchmark['tokens_per_sec']}tok/s")
        return ", ".join(parts)


# ── BatchPool ──────────────────────────────────────────────────────────────────

class BatchPool:
    """
    Secondary pool for slow/deep batch reasoning (#29).

    Uses Ollama with OLLAMA_BATCH_MODEL (default: qwen2.5:14b) on the best
    background/batch machine. Igor can `ollama pull qwen2.5:14b` to activate.
    Falls back to OpenRouter (qwen/qwen2.5-14b-instruct) when unavailable.

    Machine selection: OLLAMA_BATCH_HOST env var overrides; otherwise prefers
    wired priority.background or priority.batch machines from machines.json.
    """

    BATCH_SLOW_THRESHOLD_SECS = int(os.getenv("BATCH_SLOW_THRESHOLD_SECS", "3600"))
    BATCH_OR_MODEL            = os.getenv("BATCH_OR_MODEL", "qwen/qwen2.5-14b-instruct")
    _DURATION_WINDOW          = 5

    def __init__(self, fallback: "LocalPool | None" = None):
        self._fallback         = fallback
        self._reasoner:  OllamaReasoner | None = None
        self._batch_host: str | None = None
        self._recent_durations: list[float] = []
        self._refresh()

    def _refresh(self) -> None:
        """Find best batch-capable Ollama machine. G40: skip machines with warn/critical load."""
        # Explicit override wins
        override = os.getenv("OLLAMA_BATCH_HOST", "").strip()
        if override:
            self._batch_host = override
            self._reasoner   = OllamaReasoner(model=OLLAMA_BATCH_MODEL, host=override)
            return

        # G40: get cluster load snapshot (cached 60s — non-blocking on failure)
        _cluster_loads: dict = {}
        try:
            from ...tools.cluster_ssh import get_cluster_loads as _gcl
            _cluster_loads = _gcl()
        except Exception:
            pass  # Load awareness is best-effort; never blocks dispatch

        machines = _parse_online_machines()
        batch_order = sorted(machines, key=lambda m: (
            0 if m.get("network", "").lower() == "wired" else 1,
            PRIORITY_ORDER.get(m.get("priority", ""), 99),
        ))
        for m in batch_order:
            pri = m.get("priority", "")
            if pri not in ("priority.background", "priority.batch"):
                continue
            # G40: skip machines under warn/critical load
            load_info = _cluster_loads.get(m.get("hostname", ""), {})
            if load_info.get("verdict") in ("warn", "critical"):
                console.print(
                    f"[yellow][G40] Skipping {m.get('hostname')} for batch — "
                    f"load verdict={load_info['verdict']} "
                    f"(cpu={load_info.get('cpu',0):.0f}% "
                    f"ram={load_info.get('ram',0):.0f}%)[/]"
                )
                continue
            host = f"http://{m['ip']}:{OLLAMA_PORT}"
            self._batch_host = host
            self._reasoner   = OllamaReasoner(model=OLLAMA_BATCH_MODEL, host=host)
            return

        # Fallback: local Ollama with batch model
        self._batch_host = OLLAMA_HOST
        self._reasoner   = OllamaReasoner(model=OLLAMA_BATCH_MODEL, host=OLLAMA_HOST)

    def is_available(self) -> bool:
        return is_healthy(self._batch_host or OLLAMA_HOST, timeout=3)

    def _is_running_slow(self) -> bool:
        if len(self._recent_durations) < 2:
            return False
        return sum(self._recent_durations) / len(self._recent_durations) > self.BATCH_SLOW_THRESHOLD_SECS

    def _record_duration(self, seconds: float) -> None:
        self._recent_durations.append(seconds)
        if len(self._recent_durations) > self._DURATION_WINDOW:
            self._recent_durations.pop(0)

    def _reason_openrouter(self, user_input: str) -> tuple[str, float]:
        import urllib.request
        api_key = os.getenv("OPENROUTER_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY not set")
        payload = json.dumps({
            "model": self.BATCH_OR_MODEL,
            "messages": [{"role": "user", "content": user_input}],
            "max_tokens": 2048,
        }).encode()
        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/akienm/TheIgors",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
        text = data["choices"][0]["message"]["content"]
        cost = data.get("usage", {}).get("total_tokens", 0) * 0.000001
        return text, cost

    def reason_batch(
        self,
        user_input: str,
        relevant_memories: list,
        core_patterns: list,
        instance_id: str,
    ) -> tuple[str, float]:
        """
        Run a batch reasoning request. No latency budget.
        1. If local is running slow → prefer OpenRouter.
        2. Try local Ollama batch machine.
        3. OpenRouter fallback.
        4. LocalPool fallback (last resort).
        """
        from .forensic_logger import log_batch_call

        if self._is_running_slow():
            try:
                t0 = time.time()
                result = self._reason_openrouter(user_input)
                log_batch_call(source="openrouter", model=self.BATCH_OR_MODEL,
                               elapsed_s=time.time() - t0, via="slow_local_bypass")
                return result
            except Exception:
                pass

        if self._reasoner is not None and self.is_available():
            try:
                t0 = time.time()
                result = self._reasoner.reason(user_input, relevant_memories, core_patterns, instance_id)
                elapsed = time.time() - t0
                self._record_duration(elapsed)
                log_batch_call(source="local", model=OLLAMA_BATCH_MODEL,
                               elapsed_s=elapsed, via=self._batch_host or "")
                return result
            except Exception:
                pass

        try:
            t0 = time.time()
            result = self._reason_openrouter(user_input)
            log_batch_call(source="openrouter", model=self.BATCH_OR_MODEL,
                           elapsed_s=time.time() - t0, via="local_unavailable")
            return result
        except Exception:
            pass

        if self._fallback is not None:
            return self._fallback.reason(
                user_input, relevant_memories, core_patterns, instance_id, force_local=True,
            )
        raise RuntimeError("BatchPool: all batch paths exhausted")

    def status(self) -> str:
        slow = f"|slow={'yes' if self._is_running_slow() else 'no'}"
        avg  = (f"|avg={sum(self._recent_durations)/len(self._recent_durations):.0f}s"
                if self._recent_durations else "")
        avail = "up" if self.is_available() else "down"
        return f"batch_pool|host={self._batch_host}|model={OLLAMA_BATCH_MODEL}|{avail}{slow}{avg}"


# ── Backwards-compat aliases ───────────────────────────────────────────────────
LocalKoboldPool = LocalPool
BatchKoboldPool = BatchPool
