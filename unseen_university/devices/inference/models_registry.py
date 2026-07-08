"""
models_registry.py — Model catalog for the inference proxy mini-rack.

Each ModelSpec records: model_id (as OR/Ollama expects it), tier, and pricing.
The tier seeds the a-priori difficulty the resolver filters candidates by.
Model<->provider reachability is NOT on the ModelSpec (source_name is deleted at
the router cutover) — it lives on the connections stack (connections.py).

Tiers:
  minion   — cheapest, fast, simple transforms and boilerplate
  worker   — mid-tier, sprint tickets and coding tasks
  analyst  — larger reasoning models, research and eval
  designer — Claude via Anthropic direct, design sessions with Akien

Tag 'cacheable': model supports prefix caching (cache_control on system message).

Versioning (T-inference-models-versioned):
  ModelsRegistry keeps an in-memory version history per model.
  update_model(model_id, new_spec) archives the current entry (with retired_at
  set to now) and replaces the facia row. list_model_history(model_id) returns
  the archive in chronological order. The facia row's key (model_id) never
  changes — existing rules-engine references stay valid.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from unseen_university.devices.inference.routing_buckets import task_class_to_difficulty

TIERS = ("minion", "worker", "analyst", "designer")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ModelSpec:
    model_id: str
    tier: str
    input_cost_per_1m: float
    output_cost_per_1m: float
    context_window: int
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    created_at: str = ""  # ISO-8601 UTC — when this entry was verified/created
    # Capability ceiling for the cost-optimizing router: the hardest
    # routing_buckets.DIFFICULTY_BUCKETS bucket this model can handle. Empty →
    # derived from `tier` (the a-priori estimate). Set explicitly to describe a model
    # whose true ceiling differs from the tier slot it's routed under.
    difficulty_capable: str = ""
    # Structured capability flags the selector filters on (e.g. 'tools', 'json_mode',
    # 'vision') — distinct from free-text `tags`. A call requiring a feature excludes
    # models that lack it.
    features: list[str] = field(default_factory=list)
    # Task DOMAINS this model is competent in ('coding', 'prose', 'math', …). Empty →
    # generalist: eligible for any requested domain. Orthogonal to `tier` (difficulty,
    # HOW HARD) and to free-text `tags` — domain is WHAT KIND of task. The selector's
    # domain filter (routing_buckets.domain_eligible) reads this. Adding a model for a
    # new kind of task is a data edit here, not a code change.
    domains: list[str] = field(default_factory=list)
    # Per-edit-format conformance rate ({format: 0.0–1.0}), computed OFFLINE from corpus
    # replay (edit_format.compute_conformance) — the warm-lookup data for edit-dialect
    # selection (T-aider-port-editformat-conformance). Empty → block (the runtime ladder
    # then falls back to whole-file). Populated as both editors accumulate real runs; the
    # selector reads it with zero inference, exactly like tier routing.
    edit_format_conformance: dict = field(default_factory=dict)

    @property
    def cacheable(self) -> bool:
        return "cacheable" in self.tags

    @property
    def difficulty_bucket(self) -> str:
        """Difficulty this model can handle: explicit ceiling, else tier-derived.

        The selector's capability filter reads this; a model with no explicit
        difficulty_capable falls back to its tier's a-priori bucket
        (minion=classify, worker=code, analyst/designer=design).
        """
        return self.difficulty_capable or task_class_to_difficulty(self.tier)

    @property
    def dollars_per_unit(self) -> float:
        """Marginal-cost scalar for the selector's argmin — 0 for owned-local/flat models.

        Sum of input + output per-1M-token cost: a symmetric proxy for per-call dollars.
        The source's cost_class captures the quota/subscription dimension this marginal
        number cannot (a $0/token subscription still consumes a metered cap); the
        selector combines the two (increment 2).
        """
        return self.input_cost_per_1m + self.output_cost_per_1m

    def cost_estimate(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1_000_000 * self.input_cost_per_1m
            + output_tokens / 1_000_000 * self.output_cost_per_1m
        )


# ── Seed data — verified against openrouter.ai/api/v1/models on 2026-06-02 ───
# Pricing volatile — re-verify at openrouter.ai/models before production use.

_SEED: list[ModelSpec] = [
    # Minion tier — trivial tasks, boilerplate, simple transforms
    ModelSpec(
        model_id="qwen/qwen3.5-9b",
        tier="minion",
        input_cost_per_1m=0.04,
        output_cost_per_1m=0.15,
        context_window=262_144,
        tags=["coding", "cheap", "fast", "cacheable"],
        domains=["coding"],
        notes="Confirmed working 2026-06-02; fast and cheap; good for simple well-specified tasks",
        created_at="2026-06-02T00:00:00Z",
    ),
    # Worker tier — dedicated coding model, MoE architecture, very cheap
    ModelSpec(
        model_id="qwen/qwen3-coder-30b-a3b-instruct",
        tier="worker",
        input_cost_per_1m=0.07,
        output_cost_per_1m=0.28,
        context_window=156_000,
        tags=["coding", "fast", "cacheable"],
        domains=["coding"],
        notes="Qwen3 dedicated coder; replaces deprecated qwen2.5-coder-32b slug",
        created_at="2026-06-02T00:00:00Z",
    ),
    # Worker tier — large all-rounder fallback; strong tool-calling
    ModelSpec(
        model_id="qwen/qwen3-235b-a22b-2507",
        tier="worker",
        input_cost_per_1m=0.071,
        output_cost_per_1m=0.284,
        context_window=262_144,
        tags=["coding", "general", "large", "cacheable"],
        notes="Huge MoE model; strong reasoning; fallback when smaller coder stalls",
        created_at="2026-06-02T00:00:00Z",
    ),
    # Analyst tier — DeepSeek V4 Flash: 1M context, cheapest strong coding model
    ModelSpec(
        model_id="deepseek/deepseek-v4-flash",
        tier="analyst",
        input_cost_per_1m=0.098,
        output_cost_per_1m=0.392,
        context_window=1_048_576,
        tags=["coding", "reasoning", "1m-context", "cacheable"],
        notes="DeepSeek V4 Flash; replaces deprecated deepseek-v3 slug; 1M context, strong coding",
        created_at="2026-06-02T00:00:00Z",
    ),
    # Analyst tier — Qwen3 Coder main; 1M context, competitive with V4 Flash
    ModelSpec(
        model_id="qwen/qwen3-coder",
        tier="analyst",
        input_cost_per_1m=0.22,
        output_cost_per_1m=0.88,
        context_window=1_048_576,
        tags=["coding", "1m-context", "cacheable"],
        domains=["coding"],
        notes="Qwen3 Coder full model; 1M context; fallback analyst when DeepSeek unavailable",
        created_at="2026-06-02T00:00:00Z",
    ),
    # Designer tier — Gemini Flash free tier ($0, rate-limited ~15 RPM)
    # Boilerplate cleanup, public-repo tasks, log transforms → cost: $0.
    ModelSpec(
        model_id="gemini-2.5-flash",
        tier="designer",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
        context_window=1_048_576,
        tags=["design", "fast", "1m-context", "free-tier"],
        notes="Google AI Studio free tier. Use for boilerplate and public-repo tasks. ~15 RPM cap. "
              "Reconciled 2026-06-19 from gemini-2.0-flash (retired by Google on free-tier generateContent).",
        created_at="2026-06-19T00:00:00Z",
    ),
    # Designer tier — Gemini Flash paid (native Google API, 75% auto-cache on >32k tokens)
    # Routes through google source directly — NOT OpenRouter (would lose caching discount).
    ModelSpec(
        model_id="gemini-2.0-flash-paid",
        tier="designer",
        input_cost_per_1m=0.10,
        output_cost_per_1m=0.40,
        context_window=1_048_576,
        tags=["design", "architect", "fast", "1m-context", "cacheable"],
        notes="Native Gemini API — 75% auto-cache discount on >32k token payloads. "
              "Do NOT route through OpenRouter (loses caching). Primary paid designer tier.",
        created_at="2026-06-02T00:00:00Z",
    ),
    # Designer tier — Gemini Flash via OpenRouter (fallback only — no caching benefit)
    ModelSpec(
        model_id="google/gemini-2.0-flash",
        tier="designer",
        input_cost_per_1m=0.10,
        output_cost_per_1m=0.40,
        context_window=1_048_576,
        tags=["design", "fast", "1m-context", "or-fallback"],
        notes="OpenRouter fallback ONLY — no prompt caching. Use google_free or google source first.",
        created_at="2026-06-02T00:00:00Z",
    ),
    # Ollama Cloud (flat-rate via Ollama Pro $20/mo subscription)
    # These models are preferred over usage-based OR when the subscription is active.
    # Set OLLAMA_PRO_API_KEY to enable. Model IDs match ollama.com library names.
    ModelSpec(
        model_id="devstral-small-2:24b",
        tier="worker",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
        context_window=128_000,
        tags=["coding", "flat-rate", "ollama-pro", "agentic", "tool-call"],
        domains=["coding"],
        notes="Mistral Devstral Small 2 — purpose-built agentic coding model, 24B. Floor candidate for flat-rate worker tier.",
        created_at="2026-06-11T00:00:00Z",
    ),
    ModelSpec(
        model_id="qwen3-coder-next",
        tier="worker",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
        context_window=256_000,
        tags=["coding", "flat-rate", "ollama-pro"],
        domains=["coding"],
        notes="Ollama Pro flat-rate: Qwen3 Coder Next, dedicated coder. Reconciled 2026-06-19 "
              "from qwen2.5-coder:32b (404 — not on the account). Preferred when OLLAMA_API_KEY set.",
        created_at="2026-06-19T00:00:00Z",
    ),
    ModelSpec(
        model_id="deepseek-v4-flash",
        tier="analyst",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
        context_window=128_000,
        tags=["general", "reasoning", "flat-rate", "ollama-pro"],
        notes="Ollama Pro flat-rate: DeepSeek V4 Flash, strong reasoning/coding analyst. Reconciled "
              "2026-06-19 from llama3.3:70b (404 — not on the account). Preferred when OLLAMA_API_KEY set.",
        created_at="2026-06-19T00:00:00Z",
    ),
    # ── Hex (Mac Studio M1 Max 32GB, 10.0.0.100) — owned-local, $0, source 'ollama' ──
    # Registered 2026-07-01 (T-ds-local-ollama-route). Roster probed live against Hex's
    # /api/tags. Source 'ollama' is cost_class=owned_local + time_bucket=interactive, so
    # the cost-optimizing selector prefers these over every cloud source when Hex is up.
    # dollars=0 (owned hardware); difficulty_capable set explicitly per model. devstral
    # is NOT re-registered here — its existing spec is reused, and its reachability on the
    # 'ollama' provider is carried by a connection edge in connections.default_connections
    # (a model_id may be reachable on several providers independently of its ModelSpec).
    ModelSpec(
        model_id="llama3.2:3b",
        tier="minion",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
        context_window=131_072,
        tags=["local", "hex", "fast", "minion"],
        difficulty_capable="classify",
        notes="Hex local minion — trivial transforms/classification. 2GB, fits easily.",
        created_at="2026-07-01T00:00:00Z",
    ),
    ModelSpec(
        model_id="qwen2.5-coder:14b",
        tier="worker",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
        context_window=32_768,
        tags=["local", "hex", "coding", "worker"],
        difficulty_capable="code",
        features=["tools"],
        domains=["coding"],
        notes="Hex local worker — coder fallback behind devstral. 9GB.",
        created_at="2026-07-01T00:00:00Z",
    ),
    ModelSpec(
        model_id="deepseek-r1:14b",
        tier="analyst",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
        context_window=131_072,
        tags=["local", "hex", "reasoning", "analyst"],
        difficulty_capable="code",
        notes="Hex local analyst — 14B reasoner. Serves the analyst (reasoning) tier at the "
              "CODE difficulty rung; the bigger deepseek-r1:32b (below) is the coding domain's "
              "design/architect rung. 9GB.",
        created_at="2026-07-01T00:00:00Z",
    ),
    # ── Coding domain's full ladder (D-coding-domain-hex-cloud-ladder-2026-07-01) ──
    # Hex-local first (owned_local, $0, cheapest); Ollama Cloud subscription only for
    # the SAME-family flagships too large for Hex's 32GB RAM to hold. No OR/Anthropic/
    # Google belongs on this domain's ladder — pure Ollama, local then cloud-subscription.
    ModelSpec(
        model_id="qwen3-coder:30b",
        tier="worker",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
        context_window=256_000,
        tags=["local", "hex", "coding", "worker"],
        difficulty_capable="code",
        features=["tools"],
        domains=["coding"],
        notes="Hex local worker — bigger in-family coder above qwen2.5-coder:14b. 18GB.",
        created_at="2026-07-01T00:00:00Z",
    ),
    ModelSpec(
        model_id="deepseek-r1:32b",
        tier="analyst",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
        context_window=131_072,
        tags=["local", "hex", "reasoning", "architect"],
        difficulty_capable="design",
        domains=["coding"],
        notes="Hex local architect — fills the coding domain's design/architect rung (was "
              "empty). 19GB. First local candidate DS's escalation walk can reach at "
              "required_difficulty='design'.",
        created_at="2026-07-01T00:00:00Z",
    ),
    ModelSpec(
        model_id="qwen3-coder:480b-cloud",
        tier="worker",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
        context_window=256_000,
        tags=["coding", "flat-rate", "ollama-pro", "cloud-flagship"],
        difficulty_capable="code",
        features=["tools"],
        domains=["coding"],
        notes="Ollama Cloud subscription flagship coder — too large for Hex's 32GB RAM. "
              "Escalation target only when Hex's local coders are exhausted/unavailable.",
        created_at="2026-07-01T00:00:00Z",
    ),
    ModelSpec(
        model_id="deepseek-v3.1:671b-cloud",
        tier="analyst",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
        context_window=131_072,
        tags=["reasoning", "flat-rate", "ollama-pro", "cloud-flagship", "architect"],
        difficulty_capable="design",
        domains=["coding"],
        notes="Ollama Cloud subscription flagship architect — too large for Hex's 32GB RAM. "
              "Escalation target only when Hex's local deepseek-r1:32b is exhausted/unavailable.",
        created_at="2026-07-01T00:00:00Z",
    ),
    ModelSpec(
        model_id="anthropic/claude-haiku-4.5",
        tier="worker",
        input_cost_per_1m=0.80,
        output_cost_per_1m=4.00,
        context_window=200_000,
        tags=["worker", "claude", "fast", "cheap"],
        notes="Claude Haiku 4.5 via OpenRouter. Primary worker; strong instruction-follower for sprint-ticket.",
        created_at="2026-06-10T00:00:00Z",
    ),
    ModelSpec(
        model_id="anthropic/claude-sonnet-4.6",
        tier="worker",
        input_cost_per_1m=3.00,
        output_cost_per_1m=15.00,
        context_window=200_000,
        tags=["worker", "claude", "escalation"],
        notes="Claude Sonnet 4.6 via OpenRouter. Escalation when haiku insufficient for complex tasks.",
        created_at="2026-06-10T00:00:00Z",
    ),
    ModelSpec(
        model_id="anthropic/claude-opus-4.8",
        tier="designer",
        input_cost_per_1m=15.00,
        output_cost_per_1m=75.00,
        context_window=200_000,
        tags=["designer", "claude", "opus", "last-resort"],
        notes="Claude Opus 4.8 via OpenRouter. Last resort before CC escalation in Dick's tier cascade.",
        created_at="2026-06-10T00:00:00Z",
    ),
    ModelSpec(
        model_id="claude-sonnet-4-6",
        tier="designer",
        input_cost_per_1m=3.00,
        output_cost_per_1m=15.00,
        context_window=200_000,
        tags=["design", "architect", "claude", "heavy", "cacheable"],
        notes="Direct Anthropic API with prompt caching. Heaviest tier — use when Gemini insufficient.",
        created_at="2026-06-02T00:00:00Z",
    ),
]


class ModelsRegistry:
    """In-memory model catalog. Queryable by tier, source, or model_id.

    Versioning: update_model() archives the current entry (with retired_at
    timestamp) before replacing the facia row. list_model_history() returns
    the archive in chronological order. The facia key (model_id) is stable
    across updates so rules-engine references never break.
    """

    def __init__(self, seed: list[ModelSpec] | None = None) -> None:
        self._models: dict[str, ModelSpec] = {}
        # _history[model_id] = list of archived entries, oldest first.
        # Each entry is a dict copy of ModelSpec fields + "retired_at".
        self._history: dict[str, list[dict]] = {}
        for spec in seed or _SEED:
            self._models[spec.model_id] = spec

    def get(self, model_id: str) -> ModelSpec | None:
        return self._models.get(model_id)

    def by_tier(self, tier: str) -> list[ModelSpec]:
        """All models for a tier, sorted cheapest-input first."""
        return sorted(
            [m for m in self._models.values() if m.tier == tier],
            key=lambda m: m.input_cost_per_1m,
        )

    def cheapest_in_tier(self, tier: str) -> ModelSpec | None:
        candidates = self.by_tier(tier)
        return candidates[0] if candidates else None

    def all(self) -> list[ModelSpec]:
        return list(self._models.values())

    def register(self, spec: ModelSpec) -> None:
        self._models[spec.model_id] = spec

    def update_model(self, model_id: str, new_spec: ModelSpec) -> None:
        """Archive the current facia entry and replace it with new_spec.

        The facia key (model_id) stays stable. The archived entry gains a
        retired_at timestamp so the history is fully dated. No-ops when
        model_id is not yet registered; use register() for first-time adds.
        """
        current = self._models.get(model_id)
        if current is None:
            self._models[new_spec.model_id] = new_spec
            return
        archived = {**asdict(current), "retired_at": _now_iso()}
        self._history.setdefault(model_id, []).append(archived)
        self._models[model_id] = new_spec

    def list_model_history(self, model_id: str) -> list[dict]:
        """Return archived versions for model_id, oldest first.

        Each entry is a dict of ModelSpec fields plus 'retired_at'.
        Returns [] when no history exists or the model is unknown.
        """
        return list(self._history.get(model_id, []))


def default_registry() -> ModelsRegistry:
    return ModelsRegistry(_SEED)
