"""
Rules engine (Stack 3) — selects optimal ModelOption for each request.

Evaluates request semantics (human, background, coding, caching) against
available ModelOptions and their performance metrics.

Basic heuristics (v1):
- human + high-priority → fast tier (Sonnet+, cloud)
- background + low-priority → slow/cheap (Haiku, local with timeout)
- coding + tier specified → appropriate tier (0=Haiku, 1=Sonnet, 2=Opus)
- caching required → provider must support it

Future (v2+): Replace heuristic with graph-tree learned from metrics.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model_option import ModelOption

log = logging.getLogger(__name__)


class RulesEngine:
    """Stack 3: Routes requests to optimal ModelOption based on heuristics + metrics."""

    def __init__(self, model_options: list[ModelOption]):
        """Initialize rules engine with available model options.

        Args:
            model_options: list of ModelOption instances
        """
        self.model_options = model_options
        self._coding_tiers = self._build_coding_tiers()

    def _build_coding_tiers(self) -> dict[int, list[ModelOption]]:
        """Organize model options by coding tier.

        Tier 0: Fast, cheap (Haiku-level)
        Tier 1: Medium (Sonnet-level)
        Tier 2: Expensive, capable (Opus-level)
        """
        tiers = {0: [], 1: [], 2: []}

        for opt in self.model_options:
            # Heuristic: tier by model name or explicit capability
            if "haiku" in opt.model_name.lower():
                tiers[0].append(opt)
            elif "sonnet" in opt.model_name.lower():
                tiers[1].append(opt)
            elif "opus" in opt.model_name.lower():
                tiers[2].append(opt)
            else:
                # Default to tier 1 (medium)
                tiers[1].append(opt)

        return tiers

    def select(
        self,
        human: str | None = None,
        background: bool = False,
        coding: bool = False,
        coding_tier: int | None = None,
        caching: bool = True,
        **kwargs,
    ) -> tuple[ModelOption, str]:
        """Select optimal ModelOption for request.

        Args:
            human: caller name (indicates priority)
            background: background task (prefers slow/cheap)
            coding: coding task (selects by tier)
            coding_tier: 0=haiku, 1=sonnet, 2=opus (only if coding=True)
            caching: require caching support
            **kwargs: additional context

        Returns:
            (selected_model_option, selection_reason)
        """

        # Filter by hard constraints
        candidates = self.model_options[:]

        # Caching requirement
        if caching:
            candidates = [m for m in candidates if m.capabilities.supports_regular_caching]

        if not candidates:
            log.warning("Rules: no candidates after caching filter; falling back to all options")
            candidates = self.model_options

        # Route by request type
        if coding:
            # Coding request: tier-based selection
            tier = coding_tier or 1  # default to medium
            tier_options = self._coding_tiers.get(tier, [])
            if tier_options:
                selected = self._pick_best_performer(tier_options)
                return selected, f"coding tier {tier}"
            else:
                log.warning("Rules: no models for coding tier %d", tier)

        if background:
            # Background task: prefer slow/cheap (local, optimized for cost)
            cheap_options = [m for m in candidates if m.capabilities.cost_tier == "cheap"]
            if cheap_options:
                selected = self._pick_best_performer(cheap_options)
                return selected, "background (cheap)"
            else:
                # Fall back to best success rate
                selected = self._pick_best_performer(candidates)
                return selected, "background (best available)"

        if human:
            # Human request: fast, responsive, cloud-preferred
            fast_options = [m for m in candidates if m.capabilities.speed_tier == "fast"]
            if fast_options:
                selected = self._pick_best_performer(fast_options)
                return selected, f"human request from {human} (fast)"
            else:
                # Fall back to best performer
                selected = self._pick_best_performer(candidates)
                return selected, f"human request from {human} (best available)"

        # Default: best overall performer
        selected = self._pick_best_performer(candidates)
        return selected, "default (best overall)"

    def _pick_best_performer(self, options: list[ModelOption]) -> ModelOption:
        """Pick the best performer from a list based on metrics.

        Scoring: success_rate * (1 / latency) / cost
        """
        if not options:
            # Fallback to first available
            return self.model_options[0] if self.model_options else None

        if len(options) == 1:
            return options[0]

        def score(opt: ModelOption) -> float:
            """Score a model option: higher is better."""
            if opt.metrics.total_calls == 0:
                # No history: neutral score
                return 1.0

            success_component = max(opt.metrics.success_rate, 0.5)  # floor at 50%
            latency_component = 1.0 / (opt.metrics.avg_latency_ms / 1000.0 + 1.0)  # normalize to 0-1ish
            cost_component = 1.0 / (opt.metrics.total_cost / opt.metrics.total_calls + 0.001)  # per-call cost

            return success_component * latency_component * cost_component

        best = max(options, key=score)
        return best
