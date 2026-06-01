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
        model_id="qwen/qwen2.5-coder-32b-instruct",
        source_name="openrouter",
        tier="worker",
        input_cost_per_1m=0.07,
        output_cost_per_1m=0.12,
        context_window=131_072,
        tags=["coding", "swe-bench-verified"],
        notes="~28% SWE-bench verified; strong coding; primary worker model",
    ),
    # Analyst tier — research, eval, longer reasoning chains
    ModelSpec(
        model_id="deepseek/deepseek-v3",
        source_name="openrouter",
        tier="analyst",
        input_cost_per_1m=0.14,
        output_cost_per_1m=0.28,
        context_window=655_360,
        tags=["coding", "reasoning", "swe-bench-verified"],
        notes="~42% SWE-bench verified; near-Claude-3.5-Sonnet coding; primary analyst model",
    ),
    # Designer tier — Akien + CC design sessions; fast, long-context
    ModelSpec(
        model_id="google/gemini-2.0-flash",
        source_name="openrouter",
        tier="designer",
        input_cost_per_1m=0.10,
        output_cost_per_1m=0.40,
        context_window=1_048_576,
        tags=["design", "architect", "fast", "1m-context"],
        notes="Fast, 1M context; handles tool-result accumulation well; primary designer model",
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
