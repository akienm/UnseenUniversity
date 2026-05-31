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

Seeded with current OR pricing as of 2026-05-31.
"""

from __future__ import annotations

from dataclasses import dataclass, field

TIERS = ("minion", "worker", "analyst", "designer")


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

    def cost_estimate(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1_000_000 * self.input_cost_per_1m
            + output_tokens / 1_000_000 * self.output_cost_per_1m
        )


# ── Seed data — current as of 2026-05-31 ──────────────────────────────────────

_SEED: list[ModelSpec] = [
    # Minion tier — trivial tasks, boilerplate, simple transforms
    ModelSpec(
        model_id="qwen/qwen3.5-9b",
        source_name="openrouter",
        tier="minion",
        input_cost_per_1m=0.04,
        output_cost_per_1m=0.15,
        context_window=262_144,
        tags=["coding", "cheap", "fast"],
        notes="Fastest and cheapest; good for simple, well-specified tasks",
    ),
    # Worker tier — sprint tickets, general coding
    ModelSpec(
        model_id="deepseek/deepseek-v4-flash",
        source_name="openrouter",
        tier="worker",
        input_cost_per_1m=0.0983,
        output_cost_per_1m=0.1966,
        context_window=1_048_576,
        tags=["coding", "1m-context"],
        notes="1M context window; strong coding; primary Granny worker model",
    ),
    # Analyst tier — research, eval, longer reasoning chains
    ModelSpec(
        model_id="qwen/qwen3.6-35b-a3b",
        source_name="openrouter",
        tier="analyst",
        input_cost_per_1m=0.14,
        output_cost_per_1m=1.00,
        context_window=262_144,
        tags=["coding", "reasoning", "moe"],
        notes="35B MoE (3B active); good quality/cost for reasoning tasks",
    ),
    ModelSpec(
        model_id="mistralai/mistral-small-2603",
        source_name="openrouter",
        tier="analyst",
        input_cost_per_1m=0.15,
        output_cost_per_1m=0.60,
        context_window=262_144,
        tags=["coding", "reasoning"],
        notes="Mistral Small 4; solid alternative analyst-tier option",
    ),
    # Designer tier — Akien + CC design sessions; Anthropic direct
    ModelSpec(
        model_id="claude-haiku-4-5-20251001",
        source_name="anthropic",
        tier="designer",
        input_cost_per_1m=0.80,
        output_cost_per_1m=4.00,
        context_window=200_000,
        tags=["design", "architect", "claude"],
        notes="Current session model; fast design responses",
    ),
    ModelSpec(
        model_id="claude-sonnet-4-6",
        source_name="anthropic",
        tier="designer",
        input_cost_per_1m=3.00,
        output_cost_per_1m=15.00,
        context_window=200_000,
        tags=["design", "architect", "claude", "heavy"],
        notes="Heavier design work; use when Haiku isn't enough",
    ),
]


class ModelsRegistry:
    """In-memory model catalog. Queryable by tier, source, or model_id."""

    def __init__(self, seed: list[ModelSpec] | None = None) -> None:
        self._models: dict[str, ModelSpec] = {}
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


def default_registry() -> ModelsRegistry:
    return ModelsRegistry(_SEED)
