"""
Tests for the inference proxy mini-rack: sources, models_registry, rules_engine, health_monitor.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.inference.models_registry import (
    ModelSpec,
    ModelsRegistry,
    default_registry,
)
from unseen_university.devices.inference.sources import (
    OllamaSource,
    OpenRouterSource,
    Source,
    SourceRegistry,
)
from unseen_university.devices.inference.rules_engine import RulesEngine
from unseen_university.devices.inference.health_monitor import HealthMonitor
from unseen_university.devices.inference.shim import InferenceRequest

# ── ModelsRegistry ─────────────────────────────────────────────────────────────


def test_registry_by_tier_sorted_cheapest_first():
    reg = default_registry()
    workers = reg.by_tier("worker")
    assert workers, "worker tier should have at least one model"
    costs = [m.input_cost_per_1m for m in workers]
    assert costs == sorted(costs)


def test_registry_cheapest_in_tier():
    reg = default_registry()
    cheapest = reg.cheapest_in_tier("minion")
    assert cheapest is not None
    assert cheapest.tier == "minion"


def test_registry_get_by_id():
    reg = default_registry()
    spec = reg.get("qwen/qwen3-coder-30b-a3b-instruct")
    assert spec is not None
    assert spec.tier == "worker"


def test_registry_cost_estimate():
    spec = ModelSpec(
        model_id="test/model",
        tier="worker",
        input_cost_per_1m=1.0,
        output_cost_per_1m=2.0,
        context_window=8192,
    )
    cost = spec.cost_estimate(input_tokens=1_000_000, output_tokens=500_000)
    assert abs(cost - 2.0) < 0.001  # $1.00 input + $1.00 output


# ── SourceRegistry ─────────────────────────────────────────────────────────────


def test_source_registry_register_and_get():
    reg = SourceRegistry()
    src = MagicMock(spec=Source, name="test-source")
    src.name = "test"
    src.available = True
    reg.register(src)
    assert reg.get("test") is src


def test_source_registry_all_available_filters():
    reg = SourceRegistry()
    s1 = MagicMock(spec=Source)
    s1.name = "up"
    s1.available = True
    s2 = MagicMock(spec=Source)
    s2.name = "down"
    s2.available = False
    reg.register(s1)
    reg.register(s2)
    available = reg.all_available()
    assert len(available) == 1
    assert available[0].name == "up"


# ── RulesEngine route()/selector coverage moved to test_resolver_compose.py ──────
# The route()-based selector tests (cheapest-capable, minion-cheapest, availability
# skip, session affinity, designer pick, flat_rate preference, and batch night-mode)
# are retired: route() and the night-mode gate are deleted at the router cutover, and
# the equivalent selection coverage now lives against resolve() in test_resolver_compose.py.


# ── HealthMonitor ──────────────────────────────────────────────────────────────


def test_health_monitor_check_now_updates_availability():
    sources = SourceRegistry()
    src = MagicMock(spec=Source)
    src.name = "or"
    src.available = True
    src.check_and_update.return_value = False
    sources.register(src)

    monitor = HealthMonitor(sources, interval_sec=3600)
    results = monitor.check_now()
    assert results["or"] is False
    assert src.available is False


# ── InferenceRequest task_class field ─────────────────────────────────────────


def test_inference_request_default_task_class():
    req = InferenceRequest(messages=[{"role": "user", "content": "hi"}])
    assert req.task_class == "worker"


def test_inference_request_task_class_settable():
    req = InferenceRequest(
        messages=[{"role": "user", "content": "hi"}],
        task_class="minion",
    )
    assert req.task_class == "minion"


# ── Prompt caching ─────────────────────────────────────────────────────────────


def test_cacheable_model_wraps_system_as_content_array():
    """For cacheable models, system message uses content-array + cache_control."""
    src = OpenRouterSource()
    captured = {}

    def _fake_urlopen(req, timeout=None):
        import json

        body = json.loads(req.data)
        captured["messages"] = body["messages"]
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "cache_read_input_tokens": 80,
                },
            }
        ).encode()
        return resp

    req = InferenceRequest(
        messages=[{"role": "user", "content": "hello"}],
        system="You are a helpful coder.",
        model="deepseek/deepseek-v4-flash",
    )
    with (
        patch.object(src, "_api_key", return_value="test-key"),
        patch("urllib.request.urlopen", side_effect=_fake_urlopen),
    ):
        src.call(req)

    sys_msg = captured["messages"][0]
    assert sys_msg["role"] == "system"
    assert isinstance(
        sys_msg["content"], list
    ), "cacheable model should use content array"
    assert sys_msg["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert sys_msg["content"][0]["text"] == "You are a helpful coder."


def test_non_cacheable_model_uses_string_system():
    """Non-cacheable models get a plain string system message."""
    src = OpenRouterSource()
    captured = {}

    def _fake_urlopen(req, timeout=None):
        import json

        body = json.loads(req.data)
        captured["messages"] = body["messages"]
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            }
        ).encode()
        return resp

    req = InferenceRequest(
        messages=[{"role": "user", "content": "hello"}],
        system="You are a helpful coder.",
        model="google/gemini-2.0-flash",  # no cacheable tag
    )
    with (
        patch.object(src, "_api_key", return_value="test-key"),
        patch("urllib.request.urlopen", side_effect=_fake_urlopen),
    ):
        src.call(req)

    sys_msg = captured["messages"][0]
    assert sys_msg["role"] == "system"
    assert isinstance(
        sys_msg["content"], str
    ), "non-cacheable model should use plain string"


def test_cache_read_tokens_logged_at_debug(caplog):
    """cache_read_input_tokens > 0 produces a DEBUG log line."""
    import logging

    src = OpenRouterSource()

    def _fake_urlopen(req, timeout=None):
        import json

        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps(
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 10,
                    "cache_read_input_tokens": 80,
                },
            }
        ).encode()
        return resp

    req = InferenceRequest(
        messages=[{"role": "user", "content": "hello"}],
        model="deepseek/deepseek-v4-flash",
    )
    with (
        patch.object(src, "_api_key", return_value="test-key"),
        patch("urllib.request.urlopen", side_effect=_fake_urlopen),
        caplog.at_level(logging.DEBUG, logger="unseen_university.devices.inference.sources"),
    ):
        src.call(req)

    assert any("cache hit" in r.message.lower() for r in caplog.records)


def test_cacheable_property_on_modelspec():
    reg = default_registry()
    assert reg.get("deepseek/deepseek-v4-flash").cacheable is True
    assert reg.get("google/gemini-2.0-flash").cacheable is False
