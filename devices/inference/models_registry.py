"""
models_registry.py — Model catalog for the inference proxy mini-rack.

Each ModelSpec records: model_id (as OR/Ollama expects it), source_name,
tier, and pricing. The tier determines which models the RulesEngine considers
for a given task_class.

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

TIERS = ("minion", "worker", "analyst", "designer")


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class ModelSpec:
    model_id: str
    source_name: str
    tier: str
    input_cost_per_1m: float
    output_cost_per_1m: float
    context_window: int
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    created_at: str = ""  # ISO-8601 UTC — when this entry was verified/created

    @property
    def cacheable(self) -> bool:
        return "cacheable" in self.tags

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
        source_name="openrouter",
        tier="minion",
        input_cost_per_1m=0.04,
        output_cost_per_1m=0.15,
        context_window=262_144,
        tags=["coding", "cheap", "fast", "cacheable"],
        notes="Confirmed working 2026-06-02; fast and cheap; good for simple well-specified tasks",
        created_at="2026-06-02T00:00:00Z",
    ),
    # Worker tier — dedicated coding model, MoE architecture, very cheap
    ModelSpec(
        model_id="qwen/qwen3-coder-30b-a3b-instruct",
        source_name="openrouter",
        tier="worker",
        input_cost_per_1m=0.07,
        output_cost_per_1m=0.28,
        context_window=156_000,
        tags=["coding", "fast", "cacheable"],
        notes="Qwen3 dedicated coder; replaces deprecated qwen2.5-coder-32b slug",
        created_at="2026-06-02T00:00:00Z",
    ),
    # Worker tier — large all-rounder fallback; strong tool-calling
    ModelSpec(
        model_id="qwen/qwen3-235b-a22b-2507",
        source_name="openrouter",
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
        source_name="openrouter",
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
        source_name="openrouter",
        tier="analyst",
        input_cost_per_1m=0.22,
        output_cost_per_1m=0.88,
        context_window=1_048_576,
        tags=["coding", "1m-context", "cacheable"],
        notes="Qwen3 Coder full model; 1M context; fallback analyst when DeepSeek unavailable",
        created_at="2026-06-02T00:00:00Z",
    ),
    # Designer tier — Gemini Flash free tier ($0, rate-limited ~15 RPM)
    # Boilerplate cleanup, public-repo tasks, log transforms → cost: $0.
    ModelSpec(
        model_id="gemini-2.0-flash",
        source_name="google_free",
        tier="designer",
        input_cost_per_1m=0.0,
        output_cost_per_1m=0.0,
        context_window=1_048_576,
        tags=["design", "fast", "1m-context", "free-tier"],
        notes="Google AI Studio free tier. Use for boilerplate and public-repo tasks. ~15 RPM cap.",
        created_at="2026-06-02T00:00:00Z",
    ),
    # Designer tier — Gemini Flash paid (native Google API, 75% auto-cache on >32k tokens)
    # Routes through google source directly — NOT OpenRouter (would lose caching discount).
    ModelSpec(
        model_id="gemini-2.0-flash-paid",
        source_name="google",
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
        source_name="openrouter",
        tier="designer",
        input_cost_per_1m=0.10,
        output_cost_per_1m=0.40,
        context_window=1_048_576,
        tags=["design", "fast", "1m-context", "or-fallback"],
        notes="OpenRouter fallback ONLY — no prompt caching. Use google_free or google source first.",
        created_at="2026-06-02T00:00:00Z",
    ),
    ModelSpec(
        model_id="claude-sonnet-4-6",
        source_name="anthropic",
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

    def by_source(self, source_name: str) -> list[ModelSpec]:
        return [m for m in self._models.values() if m.source_name == source_name]

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
