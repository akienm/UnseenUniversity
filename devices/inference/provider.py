"""
BaseProvider — inference provider abstraction.

Each provider encapsulates connection, authentication, and query execution
against a specific inference service (OpenRouter, Ollama, Google AI Studio, etc).

Providers are pluggable. The inference proxy discovers and registers them,
then the rules engine selects which to use for each request.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)


class ProviderError(Exception):
    """Raised when provider call fails."""

    pass


@dataclass
class ProviderMetrics:
    """Per-request metrics captured during inference execution."""

    latency_ms: float
    cost: float
    success: bool
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class BaseProvider(ABC):
    """Base class for all inference providers.

    Subclasses implement:
    - authenticate() — verify credentials and connectivity
    - call(model, query, **kwargs) — execute inference
    - health() — check provider availability
    """

    def __init__(self, name: str, config: dict | None = None):
        """Initialize provider.

        Args:
            name: provider identifier (e.g., 'openrouter', 'ollama-local')
            config: provider-specific config (API keys, endpoints, etc)
        """
        self.name = name
        self.config = config or {}
        self._authenticated = False
        self._health_ok = False

    @abstractmethod
    def authenticate(self) -> bool:
        """Verify credentials and connectivity. Return True if OK."""
        pass

    @abstractmethod
    def call(
        self,
        model: str,
        query: str,
        prompt: str | None = None,
        max_tokens: int | None = None,
        timeout_s: int = 60,
        **kwargs,
    ) -> tuple[str, ProviderMetrics]:
        """Execute inference call.

        Args:
            model: model identifier (e.g., 'claude-3.5-haiku')
            query: user query
            prompt: system prompt (optional)
            max_tokens: output limit
            timeout_s: timeout in seconds
            **kwargs: provider-specific options

        Returns:
            (result_text, metrics)

        Raises:
            ProviderError on failure (includes metrics in exception).
        """
        pass

    @abstractmethod
    def health(self) -> dict:
        """Return provider health status.

        Returns:
            {status: 'healthy'|'degraded'|'unavailable', detail: str, ...}
        """
        pass

    @abstractmethod
    def capabilities(self) -> dict:
        """Return provider capabilities.

        Returns:
            {
                models: [list of model identifiers],
                max_tokens: int,
                supports_caching: bool,
                supports_semantic_caching: bool,
                ...
            }
        """
        pass
