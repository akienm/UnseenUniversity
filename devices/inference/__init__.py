"""
Inference proxy — intelligent model and provider selection.

Three-stack architecture:
- Stack 1 (Providers): Connection logic for each service (OR, Ollama, Google AI Studio, etc)
- Stack 2 (Models): Atomic model+provider options with performance metrics
- Stack 3 (Rules): Heuristic router selecting optimal option per request

Public interface:

    from devices.inference import inference_call, get_proxy

    # Auto-select model based on semantics
    result = inference_call(
        "what's 2+2?",
        human="Akien",
        coding=True,
        coding_tier=1,  # Sonnet-level
    )

    # Explicit provider setup (typically done at startup)
    proxy = get_proxy()
    proxy.register_provider(openrouter_provider)
    proxy.register_model_option(ModelOption(...))
    proxy.finalize()

Inference becomes invisible: callers specify what they need (human/background/coding),
the system handles which model. Metrics feed the rules engine for continuous learning.
"""

from .model_option import ModelCapabilities, ModelOption, PerformanceMetrics
from .provider import BaseProvider, ProviderError, ProviderMetrics
from .proxy import InferenceProxy, get_proxy, inference_call
from .rules import RulesEngine

__all__ = [
    "inference_call",
    "get_proxy",
    "BaseProvider",
    "ProviderError",
    "ProviderMetrics",
    "ModelOption",
    "ModelCapabilities",
    "PerformanceMetrics",
    "RulesEngine",
    "InferenceProxy",
]
