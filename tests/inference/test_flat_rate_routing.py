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
