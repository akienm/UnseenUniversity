"""
ModelOption — atomic model+provider+capabilities tuple with performance tracking.

Each ModelOption represents a selectable (model, provider) pair with:
- Static capabilities (speed, cost tier, caching support, etc)
- Dynamic performance metrics (observed latency, cost, success rate)

Stack 2.5 in the inference proxy: metrics accumulate here, feeding Stack 3 (rules engine).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ModelCapabilities:
    """Static capabilities of a model+provider pair."""

    supports_coding: bool = False
    supports_background: bool = True
    supports_semantic_caching: bool = False
    supports_regular_caching: bool = True
    speed_tier: str = "medium"  # 'fast', 'medium', 'slow'
    cost_tier: str = "medium"  # 'cheap', 'medium', 'expensive'
    max_tokens: int = 4096
    time_of_day_optimized: list[str] = field(default_factory=list)  # ['business_hours', 'overnight', ...]


@dataclass
class PerformanceMetrics:
    """Accumulated performance metrics for a model+provider pair."""

    total_calls: int = 0
    successful_calls: int = 0
    total_cost: float = 0.0
    total_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    success_rate: float = 0.0
    latest_error: str | None = None
    last_called_at: str | None = None
    time_of_day_patterns: dict[str, Any] = field(default_factory=dict)  # patterns learned


class ModelOption:
    """An atomic model+provider pair with static and dynamic properties."""

    def __init__(
        self,
        name: str,
        model_name: str,
        provider_name: str,
        provider: Any,  # BaseProvider instance
        capabilities: ModelCapabilities | None = None,
    ):
        """Initialize a model option.

        Args:
            name: option identifier (e.g., 'haiku-openrouter', 'sonnet-ollama-cloud')
            model_name: model identifier (e.g., 'claude-3.5-haiku')
            provider_name: provider identifier (e.g., 'openrouter')
            provider: BaseProvider instance
            capabilities: ModelCapabilities (defaults to sensible defaults)
        """
        self.name = name
        self.model_name = model_name
        self.provider_name = provider_name
        self.provider = provider
        self.capabilities = capabilities or ModelCapabilities()
        self.metrics = PerformanceMetrics()

    def record_call(
        self,
        success: bool,
        latency_ms: float,
        cost: float,
        error: str | None = None,
        time_of_day: str | None = None,
    ) -> None:
        """Record metrics from a call to this model+provider.

        Updates aggregated metrics used by rules engine for future routing.
        """
        self.metrics.total_calls += 1
        if success:
            self.metrics.successful_calls += 1
        else:
            self.metrics.latest_error = error

        self.metrics.total_cost += cost
        self.metrics.total_latency_ms += latency_ms
        self.metrics.success_rate = self.metrics.successful_calls / self.metrics.total_calls
        self.metrics.avg_latency_ms = self.metrics.total_latency_ms / self.metrics.total_calls

        if time_of_day:
            if time_of_day not in self.metrics.time_of_day_patterns:
                self.metrics.time_of_day_patterns[time_of_day] = {
                    "count": 0,
                    "avg_latency": 0,
                    "success_rate": 0,
                }
            pattern = self.metrics.time_of_day_patterns[time_of_day]
            pattern["count"] += 1
            pattern["avg_latency"] = (pattern.get("avg_latency", 0) * (pattern["count"] - 1) + latency_ms) / pattern["count"]
            if success:
                pattern["success_rate"] = (pattern.get("success_count", 0) + 1) / pattern["count"]
            pattern["success_count"] = pattern.get("success_count", 0) + (1 if success else 0)

    def to_dict(self) -> dict:
        """Export option as dict for logging/inspection."""
        return {
            "name": self.name,
            "model": self.model_name,
            "provider": self.provider_name,
            "capabilities": {
                "coding": self.capabilities.supports_coding,
                "background": self.capabilities.supports_background,
                "semantic_caching": self.capabilities.supports_semantic_caching,
                "speed_tier": self.capabilities.speed_tier,
                "cost_tier": self.capabilities.cost_tier,
            },
            "performance": {
                "total_calls": self.metrics.total_calls,
                "success_rate": self.metrics.success_rate,
                "avg_latency_ms": self.metrics.avg_latency_ms,
                "total_cost": self.metrics.total_cost,
            },
        }
