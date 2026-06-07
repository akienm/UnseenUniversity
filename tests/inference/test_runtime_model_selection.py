"""
T-runtime-model-selection tests.

Tests the three-stack inference proxy:
- Stack 1: Providers (connection logic)
- Stack 2: ModelOptions (model+provider atoms with metrics)
- Stack 3: Rules (heuristic routing)
"""

from __future__ import annotations

import pytest

from devices.inference import (
    BaseProvider,
    InferenceProxy,
    ModelCapabilities,
    ModelOption,
    PerformanceMetrics,
    ProviderMetrics,
    RulesEngine,
    get_proxy,
    inference_call,
)


# ── Mock Provider ──────────────────────────────────────────────────────────


class MockProvider(BaseProvider):
    """Mock provider for testing."""

    def __init__(self, name: str, config: dict | None = None):
        super().__init__(name, config)
        self.call_count = 0

    def authenticate(self) -> bool:
        self._authenticated = True
        return True

    def call(
        self,
        model: str,
        query: str,
        prompt: str | None = None,
        max_tokens: int | None = None,
        timeout_s: int = 60,
        **kwargs,
    ) -> tuple[str, ProviderMetrics]:
        self.call_count += 1
        return (
            f"Mock response to: {query[:50]}",
            ProviderMetrics(
                latency_ms=100.0,
                cost=0.01,
                success=True,
                tokens_in=10,
                tokens_out=20,
            ),
        )

    def health(self) -> dict:
        return {"status": "healthy", "detail": f"{self.name} mock"}

    def capabilities(self) -> dict:
        return {
            "models": ["mock-model"],
            "max_tokens": 4096,
            "supports_caching": True,
            "supports_semantic_caching": False,
        }


# ── Tests ──────────────────────────────────────────────────────────────────


class TestStack2ModelOptions:
    """Stack 2: ModelOption atomic units with performance tracking."""

    def test_model_option_initialization(self):
        provider = MockProvider("mock")
        caps = ModelCapabilities(supports_coding=True, speed_tier="fast")
        option = ModelOption(
            name="test-fast",
            model_name="test-model",
            provider_name="mock",
            provider=provider,
            capabilities=caps,
        )

        assert option.name == "test-fast"
        assert option.model_name == "test-model"
        assert option.capabilities.supports_coding is True

    def test_record_call_updates_metrics(self):
        provider = MockProvider("mock")
        option = ModelOption("test", "model", "mock", provider)

        option.record_call(success=True, latency_ms=150.0, cost=0.02)
        assert option.metrics.total_calls == 1
        assert option.metrics.successful_calls == 1
        assert option.metrics.success_rate == 1.0
        assert option.metrics.avg_latency_ms == 150.0

    def test_record_call_with_time_of_day(self):
        provider = MockProvider("mock")
        option = ModelOption("test", "model", "mock", provider)

        option.record_call(
            success=True,
            latency_ms=100.0,
            cost=0.01,
            time_of_day="business_hours",
        )
        assert "business_hours" in option.metrics.time_of_day_patterns
        assert option.metrics.time_of_day_patterns["business_hours"]["count"] == 1


class TestStack3RulesEngine:
    """Stack 3: Heuristic rules engine."""

    def test_rules_engine_initialization(self):
        provider = MockProvider("mock")
        option = ModelOption("test", "model", "mock", provider)
        engine = RulesEngine([option])

        assert len(engine.model_options) == 1

    def test_select_human_request(self):
        provider = MockProvider("mock")
        fast_option = ModelOption(
            "fast",
            "fast-model",
            "mock",
            provider,
            ModelCapabilities(speed_tier="fast"),
        )
        slow_option = ModelOption(
            "slow",
            "slow-model",
            "mock",
            provider,
            ModelCapabilities(speed_tier="slow"),
        )

        engine = RulesEngine([fast_option, slow_option])
        selected, reason = engine.select(human="Akien")

        assert selected.name in ("fast", "slow")
        assert "human" in reason or "fast" in reason

    def test_select_background_request(self):
        provider = MockProvider("mock")
        cheap_option = ModelOption(
            "cheap",
            "cheap-model",
            "mock",
            provider,
            ModelCapabilities(cost_tier="cheap"),
        )
        expensive_option = ModelOption(
            "expensive",
            "expensive-model",
            "mock",
            provider,
            ModelCapabilities(cost_tier="expensive"),
        )

        engine = RulesEngine([cheap_option, expensive_option])
        selected, reason = engine.select(background=True)

        assert "background" in reason or "cheap" in reason

    def test_select_coding_by_tier(self):
        provider = MockProvider("mock")
        haiku = ModelOption("haiku", "claude-3.5-haiku", "mock", provider)
        sonnet = ModelOption("sonnet", "claude-3.5-sonnet", "mock", provider)
        opus = ModelOption("opus", "claude-3.5-opus", "mock", provider)

        engine = RulesEngine([haiku, sonnet, opus])

        # Tier 1 = Sonnet
        selected, _ = engine.select(coding=True, coding_tier=1)
        assert selected.name == "sonnet"

    def test_caching_filter(self):
        provider = MockProvider("mock")
        cache_option = ModelOption(
            "with-cache",
            "model",
            "mock",
            provider,
            ModelCapabilities(supports_regular_caching=True),
        )
        no_cache = ModelOption(
            "no-cache",
            "model",
            "mock",
            provider,
            ModelCapabilities(supports_regular_caching=False),
        )

        engine = RulesEngine([cache_option, no_cache])
        selected, _ = engine.select(caching=True)

        assert selected.name == "with-cache"


class TestInferenceProxy:
    """Integration: InferenceProxy orchestrates the three stacks."""

    def test_proxy_initialization(self):
        proxy = InferenceProxy()
        provider = MockProvider("mock")
        option = ModelOption("test", "model", "mock", provider)

        proxy.register_provider(provider)
        proxy.register_model_option(option)
        proxy.finalize()

        assert proxy._initialized is True
        assert len(proxy.model_options) == 1

    def test_proxy_call_auto_selects(self):
        proxy = InferenceProxy()
        provider = MockProvider("mock")
        option = ModelOption("test", "model", "mock", provider)

        proxy.register_provider(provider)
        proxy.register_model_option(option)
        proxy.finalize()

        result = proxy.call("test query", human="Akien")

        assert result["error"] is None
        assert result["result"] is not None
        assert result["calling_packet"]["human"] == "Akien"
        assert "model_selected" in result

    def test_proxy_call_explicit_model(self):
        proxy = InferenceProxy()
        provider = MockProvider("mock")
        option = ModelOption("test", "my-model", "mock", provider)

        proxy.register_provider(provider)
        proxy.register_model_option(option)
        proxy.finalize()

        result = proxy.call("query", model="my-model")

        assert result["error"] is None
        assert result["model_selected"] == "my-model"

    def test_proxy_call_return_packet_format(self):
        proxy = InferenceProxy()
        provider = MockProvider("mock")
        option = ModelOption("test", "model", "mock", provider)

        proxy.register_provider(provider)
        proxy.register_model_option(option)
        proxy.finalize()

        result = proxy.call("query", human="Test", background=True)

        # Verify return packet structure
        assert "calling_packet" in result
        assert "result" in result
        assert "model_selected" in result
        assert "provider_used" in result
        assert "latency_ms" in result
        assert "cost" in result
        assert "error" in result

        # Verify calling_packet echo
        assert result["calling_packet"]["human"] == "Test"
        assert result["calling_packet"]["background"] is True

    def test_proxy_records_metrics(self):
        proxy = InferenceProxy()
        provider = MockProvider("mock")
        option = ModelOption("test", "model", "mock", provider)

        proxy.register_provider(provider)
        proxy.register_model_option(option)
        proxy.finalize()

        # First call
        proxy.call("query 1", human="Akien")
        assert option.metrics.total_calls == 1
        assert option.metrics.successful_calls == 1

        # Second call
        proxy.call("query 2", human="Akien")
        assert option.metrics.total_calls == 2

    def test_proxy_error_handling(self):
        proxy = InferenceProxy()

        # Try to call without finalize
        with pytest.raises(RuntimeError):
            proxy.call("query")

    def test_public_interface(self):
        """Test the public inference_call function."""
        proxy = get_proxy()
        provider = MockProvider("mock")
        option = ModelOption("test", "model", "mock", provider)

        proxy.register_provider(provider)
        proxy.register_model_option(option)
        proxy.finalize()

        result = inference_call("test", human="Akien", background=True)

        assert result["error"] is None
        assert result["calling_packet"]["human"] == "Akien"
