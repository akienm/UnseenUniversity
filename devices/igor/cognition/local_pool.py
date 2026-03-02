"""
LocalOllamaPool — distributes reasoning across available local-network Ollama instances.

Reads ~/.TheIgors/local/machines.csv to discover online machines, creates an
OllamaReasoner for each, and round-robins requests across them. Falls back to
the local instance if all remotes fail.

Machine selection: machines with a valid IP (not "OFFLINE") are treated as candidates.
Own machine (akiendelllinux / 127.0.0.1) is always included as the last fallback.

Part A: boot benchmarking — measures tokens/sec per model per machine; cached 24h.
Part B: latency budget — estimates local latency before committing; escalates if over.
Part C: RoutingWeights — session-scoped cost/speed weights adjusted by observed signals.
"""

from __future__ import annotations

import csv
import io
import json
import os
import platform
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .reasoners.ollama_reasoner import OllamaReasoner, DEFAULT_MODEL
from ..memory.models import Memory

MACHINES_CSV   = Path.home() / ".TheIgors" / "local" / "machines.csv"
BENCHMARK_DIR  = Path.home() / ".TheIgors" / "benchmarks"
OLLAMA_PORT    = 11434

BENCHMARK_TTL_HOURS     = 24
_DEFAULT_LATENCY_BUDGET = 8.0    # seconds; overridden by LATENCY_BUDGET_SECONDS

# Lower number = higher priority; realtime machines tried first
PRIORITY_ORDER: dict[str, int] = {
    "priority.realtime":    0,
    "priority.main_loop":   1,
    "priority.background":  2,
    "priority.batch":       3,
}

# Short, deterministic prompts — fast enough that benchmarking doesn't block startup
_BENCH_PROMPTS = [
    "Reply with only the word 'ready'.",
    "What is 2+2? Reply with only the number.",
    "True or false: water is wet. Reply with one word.",
]


# ── Machine CSV helpers ────────────────────────────────────────────────────────

def _parse_online_machines() -> list[dict]:
    """
    Parse machines.csv and return online machines sorted by priority.
    Priority order: realtime → main_loop → background → batch.
    """
    if not MACHINES_CSV.exists():
        return []
    try:
        text = MACHINES_CSV.read_text(encoding="utf-8")
        reader = csv.DictReader(io.StringIO(text))
        machines = []
        for row in reader:
            row = {k.strip(): v.strip() for k, v in row.items() if k}
            ip = row.get("IP", "").strip()
            if ip and ip.upper() != "OFFLINE":
                machines.append(row)
        machines.sort(key=lambda m: PRIORITY_ORDER.get(m.get("Priority", ""), 99))
        return machines
    except Exception:
        return []


def parse_capabilities(row: dict) -> list[str]:
    """Return list of capability strings from a machines.csv row."""
    raw = row.get("Capabilities", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


# ── Part A — Boot benchmarking ─────────────────────────────────────────────────

def _benchmark_path(hostname: str) -> Path:
    BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
    return BENCHMARK_DIR / f"{hostname}.json"


def _load_benchmark_cache(hostname: str, model: str) -> dict | None:
    """Load cached benchmark if it's fresh and for the current model."""
    path = _benchmark_path(hostname)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("model") != model:
            return None  # Model changed — need re-benchmark
        ts = datetime.fromisoformat(data["measured_at"])
        age_hours = (datetime.now() - ts).total_seconds() / 3600
        if age_hours > BENCHMARK_TTL_HOURS:
            return None  # Stale
        return data
    except Exception:
        return None


def _run_benchmark(model: str, host: str | None = None) -> dict:
    """
    Run _BENCH_PROMPTS through the given model and measure throughput.
    Returns {model, tokens_per_sec, avg_latency_ms, measured_at}.
    Raises on Ollama connection failure.
    """
    import ollama as _ollama
    client = _ollama.Client(host=host) if host else _ollama

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
        # eval_count = output tokens generated
        tokens_out = (
            getattr(response, "eval_count", None)
            or (response.get("eval_count") if isinstance(response, dict) else None)
            or 1
        )
        timings.append(elapsed)
        total_tokens += tokens_out

    avg_latency_ms  = (sum(timings) / len(timings)) * 1000
    total_elapsed   = sum(timings)
    tokens_per_sec  = total_tokens / total_elapsed if total_elapsed > 0 else 1.0

    return {
        "model":          model,
        "tokens_per_sec": round(tokens_per_sec, 1),
        "avg_latency_ms": round(avg_latency_ms, 1),
        "measured_at":    datetime.now().isoformat(),
    }


def _save_benchmark_cache(hostname: str, result: dict) -> None:
    try:
        _benchmark_path(hostname).write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def _init_benchmark_async(model: str, on_complete) -> None:
    """
    Load from cache if fresh; otherwise run benchmark in a daemon thread.
    on_complete(result_dict) is called when the benchmark is ready.
    """
    hostname = platform.node()
    cached = _load_benchmark_cache(hostname, model)
    if cached:
        on_complete(cached)
        return  # Cache hit — no thread needed

    def _worker():
        try:
            result = _run_benchmark(model)
            _save_benchmark_cache(hostname, result)
            on_complete(result)
            from rich.console import Console
            Console().print(
                f"[dim][BENCH] {model} @ {result['tokens_per_sec']} tok/sec "
                f"on {hostname} — latency budget now active[/]"
            )
        except Exception as e:
            from rich.console import Console
            Console().print(
                f"[dim][BENCH] benchmark failed for {model} ({e}) "
                f"— latency budget disabled until next boot[/]"
            )

    t = threading.Thread(target=_worker, daemon=True, name="ollama-benchmark")
    t.start()


# ── Part B + C — RoutingWeights ────────────────────────────────────────────────

class RoutingWeights:
    """
    Session-scoped cost/speed routing weights. Reset to .env defaults on boot.
    Adjusted by signals observed during the session (Part C).

    If weight adjustments prove consistently useful across sessions, the NE
    can compile them into PROCEDURAL memory so they become learned preferences.
    """

    CLAMP_MIN = 0.2
    CLAMP_MAX = 0.8
    STEP      = 0.05

    def __init__(self):
        cost_w  = float(os.getenv("ROUTING_WEIGHT_COST",  "0.60"))
        speed_w = float(os.getenv("ROUTING_WEIGHT_SPEED", "0.40"))
        self.cost_weight  = max(self.CLAMP_MIN, min(self.CLAMP_MAX, cost_w))
        self.speed_weight = max(self.CLAMP_MIN, min(self.CLAMP_MAX, speed_w))

    def adjust(self, signal: str) -> None:
        """
        Adjust weights based on observed pressure signal.

        speed_pressure   → more weight on speed (tolerate higher cost)
        cost_pressure    → more weight on cost (tolerate slower responses)
        quality_pressure → no weight change (handled by complexity skip_to=tier.4)
        """
        if signal == "speed_pressure":
            self.speed_weight = min(self.CLAMP_MAX, self.speed_weight + self.STEP)
            self.cost_weight  = max(self.CLAMP_MIN, self.cost_weight  - self.STEP)
        elif signal == "cost_pressure":
            self.cost_weight  = min(self.CLAMP_MAX, self.cost_weight  + self.STEP)
            self.speed_weight = max(self.CLAMP_MIN, self.speed_weight - self.STEP)
        # quality_pressure: no adjustment here

    def score_local(self, est_latency: float, budget: float) -> tuple[float, float, float]:
        """
        Compute (cost_score, speed_score, tier_score) for the local tier.
        cost_score  = 1.0 (local is always free).
        speed_score = 1.0 - (est_latency / budget), clamped [0, 1].
        tier_score  = weighted combination.
        """
        cost_score  = 1.0
        speed_score = max(0.0, 1.0 - (est_latency / budget)) if budget > 0 else 0.0
        tier_score  = (cost_score * self.cost_weight) + (speed_score * self.speed_weight)
        return cost_score, speed_score, tier_score

    def __repr__(self) -> str:
        return f"weights(cost={self.cost_weight:.2f},speed={self.speed_weight:.2f})"


# ── Pool ───────────────────────────────────────────────────────────────────────

class LocalOllamaPool:
    """
    Round-robin pool of OllamaReasoner instances across network machines.

    Part A: self._benchmark populated async from ~/.TheIgors/benchmarks/
    Part B: reason() estimates local latency; escalates if > LATENCY_BUDGET_SECONDS
    Part C: self.weights adjusted by signal observations from main.py
    """

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model        = model
        self._reasoners:  list[OllamaReasoner] = []
        self._index       = 0
        self._benchmark:  dict | None = None
        self.weights      = RoutingWeights()
        self._refresh()
        _init_benchmark_async(model, self._on_benchmark_done)

    def _on_benchmark_done(self, result: dict) -> None:
        self._benchmark = result

    def _refresh(self):
        """Rebuild reasoner list from machines.csv."""
        machines = _parse_online_machines()
        reasoners = []
        for m in machines:
            ip   = m.get("IP", "")
            host = f"http://{ip}:{OLLAMA_PORT}"
            reasoners.append(OllamaReasoner(model=self.model, host=host))
        reasoners.append(OllamaReasoner(model=self.model, host=None))
        self._reasoners = reasoners
        self._index     = 0

    def _next_reasoner(self) -> Iterator[OllamaReasoner]:
        n = len(self._reasoners)
        for i in range(n):
            yield self._reasoners[(self._index + i) % n]

    def _estimate_latency(self, user_input: str) -> float | None:
        """
        Estimate response latency (seconds) using benchmark throughput.
        Uses prompt_tokens as a proxy per the ticket spec.
        Returns None if benchmark is not yet available.
        """
        if self._benchmark is None:
            return None
        tps = self._benchmark.get("tokens_per_sec", 0)
        if tps <= 0:
            return None
        prompt_tokens = max(1, len(user_input) // 4)
        return prompt_tokens / tps

    def reason(
        self,
        user_input: str,
        relevant_memories: list[Memory],
        core_patterns: list[Memory],
        instance_id: str,
    ) -> tuple[str, float]:
        """
        Try each machine in round-robin order; fall back on failure.

        Before executing: estimates latency and scores the local tier.
        If estimated latency > LATENCY_BUDGET_SECONDS, raises so the caller
        (main._process_inner) escalates to the cloud chain.
        """
        from .forensic_logger import log_routing_decision

        budget      = float(os.getenv("LATENCY_BUDGET_SECONDS", str(_DEFAULT_LATENCY_BUDGET)))
        est_latency = self._estimate_latency(user_input)

        cost_score = speed_score = tier_score = 0.0
        if est_latency is not None:
            cost_score, speed_score, tier_score = self.weights.score_local(est_latency, budget)

            if est_latency > budget:
                log_routing_decision(
                    est_latency_s=est_latency,
                    budget_s=budget,
                    tier_selected="tier.2",
                    cost_score=cost_score,
                    speed_score=speed_score,
                    tier_score=tier_score,
                    escalated=True,
                    weights=repr(self.weights),
                )
                raise RuntimeError(
                    f"Estimated local latency {est_latency:.1f}s > budget {budget}s "
                    f"(speed_score={speed_score:.2f}) — escalating to cloud"
                )

        t0       = time.perf_counter()
        last_exc = None

        for reasoner in self._next_reasoner():
            try:
                result         = reasoner.reason(user_input, relevant_memories, core_patterns, instance_id)
                actual_latency = time.perf_counter() - t0
                self._index    = (self._index + 1) % len(self._reasoners)
                log_routing_decision(
                    est_latency_s=est_latency,
                    actual_latency_s=actual_latency,
                    budget_s=budget,
                    tier_selected="tier.2",
                    cost_score=cost_score,
                    speed_score=speed_score,
                    tier_score=tier_score,
                    escalated=False,
                    weights=repr(self.weights),
                )
                return result
            except Exception as exc:
                last_exc = exc
                continue

        raise RuntimeError(
            f"All {len(self._reasoners)} Ollama instances failed. "
            f"Last error: {last_exc}"
        )

    def set_model(self, model: str) -> str:
        """Switch model on all pool members. Triggers re-benchmark if model changed."""
        if model != self.model:
            self.model      = model
            self._benchmark = None  # Stale — re-benchmark for new model
            _init_benchmark_async(model, self._on_benchmark_done)
        for r in self._reasoners:
            r.model = model
        return model

    def machines_summary(self) -> str:
        """Human-readable list of pool members with priority."""
        machines = _parse_online_machines()
        parts = []
        for m in machines:
            pri  = m.get("Priority", "?").replace("priority.", "")
            caps = m.get("Capabilities", "")
            parts.append(f"{m['Hostname']}({pri})[{caps}]")
        parts.append("localhost(fallback)")
        if self._benchmark:
            parts.append(f"bench={self._benchmark['tokens_per_sec']}tok/s")
        return ", ".join(parts)
