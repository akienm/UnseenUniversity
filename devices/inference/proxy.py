"""
Inference proxy — main interface for inference calls.

Implements the public protocol:
    result_dict = inference_call(
        query,
        model=None,          # null = let rules engine select
        human=None,          # caller identity
        background=False,    # background task (slow/cheap)
        coding=False,        # coding task
        coding_tier=None,    # 0=haiku, 1=sonnet, 2=opus
        caching=True,
        prompt=None,
        max_tokens=None,
        timeout_s=60,
        ...
    )

Return packet:
    {
        "calling_packet": {all input echoed},
        "result": "...",
        "model_selected": "...",
        "provider_used": "...",
        "latency_ms": 1250,
        "cost": 0.05,
        "error": None,  # or "CRITICAL ERROR: INFERENCE FAILURE ..."
    }

The system learns from every call. Metrics feed the rules engine
for smarter selection in the future.

Inference becomes invisible: callers just call with semantics,
the proxy handles the complexity and learns automatically.
"""

from __future__ import annotations

import inspect
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from unseen_university.channel import post_to_channel

from .model_option import ModelOption
from .provider import BaseProvider, ProviderError
from .rules import RulesEngine

log = logging.getLogger(__name__)


class InferenceProxy:
    """Main inference proxy instance. Singleton per application."""

    def __init__(self):
        """Initialize proxy with registered providers and models."""
        self.providers: dict[str, BaseProvider] = {}
        self.model_options: list[ModelOption] = []
        self.rules_engine: RulesEngine | None = None
        self._initialized = False

    def register_provider(self, provider: BaseProvider) -> None:
        """Register a provider (OpenRouter, Ollama, Google AI Studio, etc)."""
        self.providers[provider.name] = provider
        if provider.authenticate():
            log.info("InferenceProxy: registered provider %s", provider.name)
        else:
            log.warning("InferenceProxy: provider %s failed auth", provider.name)

    def register_model_option(self, option: ModelOption) -> None:
        """Register a model+provider option."""
        self.model_options.append(option)
        log.debug("InferenceProxy: registered model option %s", option.name)

    def finalize(self) -> None:
        """Finalize initialization. Must be called after all providers/models registered."""
        if not self.model_options:
            raise RuntimeError("InferenceProxy: no model options registered")
        self.rules_engine = RulesEngine(self.model_options)
        self._initialized = True
        log.info("InferenceProxy: finalized with %d models", len(self.model_options))

    def call(
        self,
        query: str,
        model: str | None = None,
        human: str | None = None,
        background: bool = False,
        coding: bool = False,
        coding_tier: int | None = None,
        caching: bool = True,
        prompt: str | None = None,
        max_tokens: int | None = None,
        timeout_s: int = 60,
        **kwargs,
    ) -> dict[str, Any]:
        """Execute inference call with automatic model selection.

        Args:
            query: user query/prompt
            model: specific model to use (None = auto-select, use only for testing)
            human: caller name (enables priority routing + multi-user context)
            background: background task (prefers slow/cheap models)
            coding: coding task (routes to appropriate coding tier)
            coding_tier: 0=haiku, 1=sonnet, 2=opus (only if coding=True)
            caching: require caching support from provider
            prompt: system prompt
            max_tokens: output token limit
            timeout_s: timeout in seconds
            **kwargs: additional provider-specific options

        Returns:
            {
                "calling_packet": {all input echoed},
                "result": "...",
                "model_selected": "...",
                "provider_used": "...",
                "latency_ms": 1250,
                "cost": 0.05,
                "error": None,  # or error message
            }
        """

        if not self._initialized:
            raise RuntimeError("InferenceProxy: not finalized; call finalize() first")

        # Build calling packet for echo in return
        calling_packet = {
            "query": query,
            "model": model,
            "human": human,
            "background": background,
            "coding": coding,
            "coding_tier": coding_tier,
            "caching": caching,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "timeout_s": timeout_s,
            **kwargs,
        }

        # Caller identity (via stack inspection if not provided)
        if not human:
            human = self._get_caller_name()

        t0 = None
        try:
            # Select model via explicit override or rules engine
            if model:
                # Explicit model (testing/override): find it directly
                selected_option = next(
                    (m for m in self.model_options if m.model_name == model),
                    None,
                )
                if not selected_option:
                    raise ValueError(f"Model not found: {model}")
                selection_reason = "explicit override"
            else:
                # Auto-select via rules engine
                selected_option, selection_reason = self.rules_engine.select(
                    human=human,
                    background=background,
                    coding=coding,
                    coding_tier=coding_tier,
                    caching=caching,
                    **kwargs,
                )

            log.info(
                "InferenceProxy: selected %s (reason: %s)",
                selected_option.name,
                selection_reason,
            )

            # Execute inference
            t0 = time.time()
            result_text, provider_metrics = selected_option.provider.call(
                model=selected_option.model_name,
                query=query,
                prompt=prompt,
                max_tokens=max_tokens,
                timeout_s=timeout_s,
                **kwargs,
            )
            latency_ms = (time.time() - t0) * 1000

            # Record metrics
            selected_option.record_call(
                success=True,
                latency_ms=latency_ms,
                cost=provider_metrics.cost,
                time_of_day=self._get_time_of_day(),
            )

            return {
                "calling_packet": calling_packet,
                "result": result_text,
                "model_selected": selected_option.model_name,
                "provider_used": selected_option.provider_name,
                "model_option": selected_option.name,
                "selection_reason": selection_reason,
                "latency_ms": latency_ms,
                "cost": provider_metrics.cost,
                "tokens_in": provider_metrics.tokens_in,
                "tokens_out": provider_metrics.tokens_out,
                "error": None,
            }

        except Exception as exc:
            log.exception("InferenceProxy: call failed")
            error_msg = f"CRITICAL ERROR: INFERENCE FAILURE {type(exc).__name__}: {str(exc)[:200]}"
            self._post_error_to_channel(error_msg)
            self._post_error_to_health()

            return {
                "calling_packet": calling_packet,
                "result": None,
                "model_selected": None,
                "provider_used": None,
                "error": error_msg,
                "latency_ms": (time.time() - t0) * 1000 if t0 is not None else None,
                "cost": 0.0,
            }

    def _get_caller_name(self) -> str:
        """Inspect call stack to find caller's .name attribute."""
        frame = inspect.currentframe()
        try:
            while frame:
                local_self = frame.f_locals.get("self")
                if local_self and hasattr(local_self, "name"):
                    return local_self.name
                frame = frame.f_back
            return "unknown"
        finally:
            del frame

    def _get_time_of_day(self) -> str:
        """Categorize current time for performance tracking."""
        hour = datetime.now(timezone.utc).hour
        if 8 <= hour < 18:
            return "business_hours"
        elif 18 <= hour < 22:
            return "evening"
        else:
            return "overnight"

    def _post_error_to_channel(self, error_msg: str) -> None:
        """Post error to public channel for visibility."""
        try:
            post_to_channel(
                f"INFERENCE_ERROR: {error_msg}",
                author="inference-proxy",
                channel="shared",
            )
        except Exception as e:
            log.warning("Failed to post error to channel: %s", e)

    def _post_error_to_health(self) -> None:
        """Post error to health/status system."""
        # TODO: wire to health page / rack status when available
        pass


# Singleton instance
_inference_proxy: InferenceProxy | None = None


def get_proxy() -> InferenceProxy:
    """Get the global inference proxy instance."""
    global _inference_proxy
    if _inference_proxy is None:
        _inference_proxy = InferenceProxy()
    return _inference_proxy


def inference_call(
    query: str,
    model: str | None = None,
    human: str | None = None,
    background: bool = False,
    coding: bool = False,
    coding_tier: int | None = None,
    caching: bool = True,
    prompt: str | None = None,
    max_tokens: int | None = None,
    timeout_s: int = 60,
    **kwargs,
) -> dict[str, Any]:
    """Public interface for inference calls.

    Inference becomes invisible: just call with semantics, the system handles it.
    """
    proxy = get_proxy()
    return proxy.call(
        query=query,
        model=model,
        human=human,
        background=background,
        coding=coding,
        coding_tier=coding_tier,
        caching=caching,
        prompt=prompt,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
        **kwargs,
    )
