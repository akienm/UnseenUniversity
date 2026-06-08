"""
Tests for T-flat-rate-provider-routing:
  - billing_type field on Source
  - OllamaCloudSource (flat_rate, disabled without key)
  - RulesEngine prefers flat_rate over usage_based within same tier
  - OpenRouterSource forwards tools to API payload (incidental fix)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from devices.inference.models_registry import ModelSpec, ModelsRegistry, default_registry
from devices.inference.rules_engine import RoutingRule, RulesEngine
from devices.inference.sources import (
    OllamaCloudSource,
    OpenRouterSource,
    Source,
    SourceRegistry,
)
from devices.inference.shim import InferenceRequest


# ── billing_type field ────────────────────────────────────────────────────────


def test_source_billing_type_defaults_to_usage_based():
    """Source.billing_type defaults to usage_based."""
    src = MagicMock(spec=Source)
    src.billing_type = Source.__dataclass_fields__["billing_type"].default
    assert src.billing_type == "usage_based"


def test_openrouter_source_is_usage_based():
    src = OpenRouterSource()
    assert src.billing_type == "usage_based"


# ── OllamaCloudSource ─────────────────────────────────────────────────────────


def test_ollama_cloud_source_billing_type():
    """OllamaCloudSource.billing_type must be flat_rate."""
    with patch.dict("os.environ", {"OLLAMA_PRO_API_KEY": "test-key"}):
        src = OllamaCloudSource()
    assert src.billing_type == "flat_rate"


def test_ollama_cloud_source_disabled_without_api_key():
    """OllamaCloudSource is unavailable when OLLAMA_PRO_API_KEY not set."""
    env = {k: v for k, v in __import__("os").environ.items() if k != "OLLAMA_PRO_API_KEY"}
    with patch.dict("os.environ", env, clear=True):
        src = OllamaCloudSource()
    assert src.available is False


def test_ollama_cloud_source_available_with_api_key():
    """OllamaCloudSource is available when OLLAMA_PRO_API_KEY is set."""
    with patch.dict("os.environ", {"OLLAMA_PRO_API_KEY": "sk-test-123"}):
        src = OllamaCloudSource()
    assert src.available is True


def test_ollama_cloud_source_ping_returns_false_without_key():
    """ping() returns False immediately when no API key is configured."""
    env = {k: v for k, v in __import__("os").environ.items() if k != "OLLAMA_PRO_API_KEY"}
    with patch.dict("os.environ", env, clear=True):
        src = OllamaCloudSource()
        assert src.ping() is False


def test_ollama_cloud_source_call_includes_tools():
    """OllamaCloudSource.call() forwards req.tools to the API payload."""
    from devices.dicksimnel.toolloop import TOOL_DEFINITIONS

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }).encode()
        return resp

    with patch.dict("os.environ", {"OLLAMA_PRO_API_KEY": "sk-test"}):
        src = OllamaCloudSource()

    req = InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="qwen2.5-coder:32b",
        tools=TOOL_DEFINITIONS,
    )
    with (
        patch.object(src, "_api_key", return_value="sk-test"),
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
    ):
        src.call(req)

    assert "tools" in captured["body"], "tools must be forwarded to Ollama Cloud API"
    assert len(captured["body"]["tools"]) == len(TOOL_DEFINITIONS)


# ── RulesEngine flat-rate preference ─────────────────────────────────────────


def _make_sources(flat_rate_available: bool, usage_available: bool) -> SourceRegistry:
    reg = SourceRegistry()

    flat_src = MagicMock(spec=Source)
    flat_src.name = "ollama_cloud"
    flat_src.available = flat_rate_available
    flat_src.billing_type = "flat_rate"
    reg.register(flat_src)

    usage_src = MagicMock(spec=Source)
    usage_src.name = "openrouter"
    usage_src.available = usage_available
    usage_src.billing_type = "usage_based"
    reg.register(usage_src)

    return reg, flat_src, usage_src


def _make_models() -> ModelsRegistry:
    return ModelsRegistry([
        ModelSpec("flat-model", "ollama_cloud", "worker", 0.0, 0.0, 8192),
        ModelSpec("usage-model", "openrouter", "worker", 0.10, 0.40, 8192),
    ])


def _rules() -> list[RoutingRule]:
    return [
        # flat_rate rule has HIGHER priority number (lower priority) — but should still win
        RoutingRule(10, "worker", "flat-model", "ollama_cloud", "worker→flat/ollama-pro"),
        RoutingRule(2, "worker", "usage-model", "openrouter", "worker→usage/OR"),
    ]


def test_flat_rate_preferred_over_usage_based():
    """flat_rate source wins over usage_based even with a higher priority number."""
    sources, flat_src, _ = _make_sources(flat_rate_available=True, usage_available=True)
    engine = RulesEngine(sources, _make_models(), _rules())
    decision = engine.route("worker")
    assert decision is not None
    assert decision.source is flat_src
    assert decision.model.model_id == "flat-model"


def test_usage_based_wins_when_flat_rate_unavailable():
    """Falls back to usage_based when flat_rate source is unavailable."""
    sources, _, usage_src = _make_sources(flat_rate_available=False, usage_available=True)
    engine = RulesEngine(sources, _make_models(), _rules())
    decision = engine.route("worker")
    assert decision is not None
    assert decision.source is usage_src
    assert decision.model.model_id == "usage-model"


def test_no_candidates_returns_none():
    """Returns None when no source is available for the task_class."""
    sources, _, _ = _make_sources(flat_rate_available=False, usage_available=False)
    engine = RulesEngine(sources, _make_models(), _rules())
    decision = engine.route("worker")
    assert decision is None


def test_flat_rate_at_priority_99_beats_usage_at_priority_1():
    """billing_type dominates over priority number."""
    sources, flat_src, _ = _make_sources(flat_rate_available=True, usage_available=True)
    rules = [
        RoutingRule(99, "worker", "flat-model", "ollama_cloud", "flat-last"),
        RoutingRule(1, "worker", "usage-model", "openrouter", "usage-first"),
    ]
    engine = RulesEngine(sources, _make_models(), rules)
    decision = engine.route("worker")
    assert decision.source is flat_src


# ── OpenRouterSource tools forwarding (incidental fix) ────────────────────────


def test_openrouter_forwards_tools_to_api():
    """OpenRouterSource.call() must include req.tools in the POST payload."""
    from devices.dicksimnel.toolloop import TOOL_DEFINITIONS

    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }).encode()
        return resp

    src = OpenRouterSource()
    req = InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="qwen/qwen3-coder-30b-a3b-instruct",
        tools=TOOL_DEFINITIONS,
    )
    with (
        patch.object(src, "_api_key", return_value="test-key"),
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
    ):
        src.call(req)

    assert "tools" in captured["body"], "tools must reach the OR API"
    assert len(captured["body"]["tools"]) == len(TOOL_DEFINITIONS)


def test_openrouter_no_tools_field_when_tools_none():
    """OpenRouterSource.call() must NOT include 'tools' key when req.tools is None."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["body"] = json.loads(req.data)
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }).encode()
        return resp

    src = OpenRouterSource()
    req = InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        model="qwen/qwen3-coder-30b-a3b-instruct",
        tools=None,
    )
    with (
        patch.object(src, "_api_key", return_value="test-key"),
        patch("urllib.request.urlopen", side_effect=fake_urlopen),
    ):
        src.call(req)

    assert "tools" not in captured["body"]


# ── GoogleSource free-tier billing_type (T-inference-proxy-mini-rack) ─────────


def test_google_free_source_billing_type_is_flat_rate():
    """GoogleSource(free_tier=True) must be flat_rate so rules engine prefers it over OR."""
    from devices.inference.sources import GoogleSource
    src = GoogleSource(free_tier=True)
    assert src.billing_type == "flat_rate", (
        "google_free must be flat_rate — otherwise worker tasks route to paid OR "
        "even when Google AI Studio key is set"
    )


def test_google_paid_source_billing_type_is_usage_based():
    """GoogleSource(free_tier=False) is usage_based — billed per token."""
    from devices.inference.sources import GoogleSource
    src = GoogleSource(free_tier=False)
    assert src.billing_type == "usage_based"


def test_worker_routes_to_google_free_when_ollama_cloud_unavailable():
    """Production path: ollama_cloud unavailable (no key), google_free available → worker uses free tier.

    This is the DickSimnel use case: OLLAMA_PRO_API_KEY unset, GOOGLE_STUDIO_API_KEY set.
    Worker tasks must NOT fall through to paid OpenRouter.
    """
    import os
    from devices.inference.models_registry import ModelSpec, ModelsRegistry
    from devices.inference.rules_engine import RoutingRule, RulesEngine
    from devices.inference.sources import Source, SourceRegistry

    # Simulate production: google_free available, ollama_cloud NOT available, OR available
    reg = SourceRegistry()

    google_src = MagicMock(spec=Source)
    google_src.name = "google_free"
    google_src.available = True
    google_src.billing_type = "flat_rate"  # the fix
    reg.register(google_src)

    ollama_src = MagicMock(spec=Source)
    ollama_src.name = "ollama_cloud"
    ollama_src.available = False  # no OLLAMA_PRO_API_KEY
    ollama_src.billing_type = "flat_rate"
    reg.register(ollama_src)

    or_src = MagicMock(spec=Source)
    or_src.name = "openrouter"
    or_src.available = True
    or_src.billing_type = "usage_based"
    reg.register(or_src)

    models = ModelsRegistry([
        ModelSpec("gemini-2.0-flash", "google_free", "worker", 0.0, 0.0, 1_048_576),
        ModelSpec("qwen2.5-coder:32b", "ollama_cloud", "worker", 0.0, 0.0, 32768),
        ModelSpec("qwen/qwen3-coder-30b-a3b-instruct", "openrouter", "worker", 0.07, 0.28, 156_000),
    ])

    rules = [
        RoutingRule(2, "worker", "qwen/qwen3-coder-30b-a3b-instruct", "openrouter", "worker→qwen3-coder/OR"),
        RoutingRule(3, "worker", "gemini-2.0-flash", "google_free", "worker→gemini-flash/google-free"),
        RoutingRule(10, "worker", "qwen2.5-coder:32b", "ollama_cloud", "worker→qwen2.5/ollama-pro"),
    ]

    engine = RulesEngine(reg, models, rules)
    decision = engine.route("worker")

    assert decision is not None
    assert decision.source is google_src, (
        f"Expected google_free (flat_rate, free) but got {decision.source.name!r} — "
        "worker tasks must prefer free providers over paid OR"
    )
    assert decision.model.model_id == "gemini-2.0-flash"
