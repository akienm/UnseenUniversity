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

from unseen_university.devices.inference.sources import (
    OllamaCloudSource,
    OpenRouterSource,
    Source,
    SourceRegistry,
)
from unseen_university.devices.inference.shim import InferenceRequest


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
    """OllamaCloudSource is unavailable when no API key in env or credentials file."""
    env = {k: v for k, v in __import__("os").environ.items()
           if k not in ("OLLAMA_PRO_API_KEY", "OLLAMA_API_KEY")}
    with patch.dict("os.environ", env, clear=True):
        with patch("unseen_university.devices.inference.sources._read_akien_cred", return_value=""):
            src = OllamaCloudSource()
    assert src.available is False


def test_ollama_cloud_source_available_with_api_key():
    """OllamaCloudSource is available when OLLAMA_PRO_API_KEY is set."""
    with patch.dict("os.environ", {"OLLAMA_PRO_API_KEY": "sk-test-123"}):
        src = OllamaCloudSource()
    assert src.available is True


def test_ollama_cloud_source_ping_returns_false_without_key():
    """ping() returns False immediately when no API key is configured."""
    env = {k: v for k, v in __import__("os").environ.items()
           if k not in ("OLLAMA_PRO_API_KEY", "OLLAMA_API_KEY")}
    with patch.dict("os.environ", env, clear=True), \
         patch("unseen_university.devices.inference.sources._read_akien_cred", return_value=""):
        src = OllamaCloudSource()
        assert src.ping() is False


def test_ollama_cloud_source_call_includes_tools():
    """OllamaCloudSource.call() forwards req.tools to the API payload."""
    from unseen_university.agentic.loop import TOOL_DEFINITIONS

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
# The route()-based flat_rate-preference and priority-domination tests are retired:
# route() is deleted at the router cutover and billing_type no longer drives selection
# (resolve() sorts by cost_class then per-connection dollars — cost_class ordering, not
# a flat_rate-vs-usage_based bias). Cheapest-capable selection and availability skip are
# covered against resolve() in test_resolver_compose.py.


# ── OpenRouterSource tools forwarding (incidental fix) ────────────────────────


def test_openrouter_forwards_tools_to_api():
    """OpenRouterSource.call() must include req.tools in the POST payload."""
    from unseen_university.agentic.loop import TOOL_DEFINITIONS

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
    from unseen_university.devices.inference.sources import GoogleSource
    src = GoogleSource(free_tier=True)
    assert src.billing_type == "flat_rate", (
        "google_free must be flat_rate — otherwise worker tasks route to paid OR "
        "even when Google AI Studio key is set"
    )


def test_google_paid_source_billing_type_is_usage_based():
    """GoogleSource(free_tier=False) is usage_based — billed per token."""
    from unseen_university.devices.inference.sources import GoogleSource
    src = GoogleSource(free_tier=False)
    assert src.billing_type == "usage_based"


# ── foreground=True routing ───────────────────────────────────────────────────
# The foreground= parameter and its route()-based tests are deleted at the router
# cutover: resolve() expresses speed as an urgency/time-eligibility filter (not a
# foreground flag) and cost as cost_class ordering. The "worker prefers the free
# provider over paid OR" outcome is now a cheapest-capable-connection result, covered
# against resolve() in test_resolver_compose.py.
