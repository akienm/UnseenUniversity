"""Tests for inference device dispatch — caching and routing."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from unseen_university.devices.inference.device import InferenceDevice
from unseen_university.devices.inference.models_registry import ModelSpec, ModelsRegistry
from unseen_university.devices.inference.shim import InferenceRequest
from unseen_university.devices.inference.sources import SourceRegistry


# ── Helpers ───────────────────────────────────────────────────────────────────


def _spec(model_id: str, cacheable: bool = False) -> ModelSpec:
    tags = ["cacheable"] if cacheable else []
    return ModelSpec(
        model_id=model_id,
        source_name="openrouter",
        tier="worker",
        input_cost_per_1m=0.1,
        output_cost_per_1m=0.4,
        context_window=128_000,
        tags=tags,
    )


def _make_device(specs: list[ModelSpec]) -> InferenceDevice:
    registry = ModelsRegistry(seed=specs)
    sources = SourceRegistry()
    return InferenceDevice(mode="openrouter", endpoint=None, sources=sources, models=registry)


def _fake_or_response(text: str = "ok") -> MagicMock:
    body = json.dumps({
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "model": "test-model",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }).encode()
    resp = MagicMock()
    resp.read.return_value = body
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=resp)
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


def _captured_payload(urlopen_mock) -> dict:
    req_obj = urlopen_mock.call_args[0][0]
    return json.loads(req_obj.data)


# ── Tests: prompt caching in _or_call ─────────────────────────────────────────


class TestOrCallCaching:
    def test_cacheable_model_with_system_adds_cache_control(self):
        device = _make_device([_spec("qwen/qwen3-coder", cacheable=True)])
        req = InferenceRequest(
            messages=[{"role": "user", "content": "hello"}],
            model="qwen/qwen3-coder",
            system="You are a helpful assistant.",
        )
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}), \
             patch("urllib.request.urlopen", return_value=_fake_or_response()) as mock_open:
            device._or_call(req)

        payload = _captured_payload(mock_open)
        system_msg = payload["messages"][0]
        assert system_msg["role"] == "system"
        assert isinstance(system_msg["content"], list), "cacheable system must be a content block list"
        block = system_msg["content"][0]
        assert block["type"] == "text"
        assert block["text"] == "You are a helpful assistant."
        assert block["cache_control"] == {"type": "ephemeral"}

    def test_non_cacheable_model_no_cache_control(self):
        device = _make_device([_spec("some/model", cacheable=False)])
        req = InferenceRequest(
            messages=[{"role": "user", "content": "hello"}],
            model="some/model",
            system="You are a helpful assistant.",
        )
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}), \
             patch("urllib.request.urlopen", return_value=_fake_or_response()) as mock_open:
            device._or_call(req)

        payload = _captured_payload(mock_open)
        system_msg = payload["messages"][0]
        assert isinstance(system_msg["content"], str), "non-cacheable system stays as plain string"

    def test_no_system_message_no_cache_control(self):
        device = _make_device([_spec("qwen/qwen3-coder", cacheable=True)])
        req = InferenceRequest(
            messages=[{"role": "user", "content": "hello"}],
            model="qwen/qwen3-coder",
        )
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}), \
             patch("urllib.request.urlopen", return_value=_fake_or_response()) as mock_open:
            device._or_call(req)

        payload = _captured_payload(mock_open)
        assert payload["messages"][0]["role"] == "user"

    def test_unknown_model_falls_back_to_plain_string(self):
        device = _make_device([])
        req = InferenceRequest(
            messages=[{"role": "user", "content": "hello"}],
            model="unknown/model",
            system="system prompt",
        )
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}), \
             patch("urllib.request.urlopen", return_value=_fake_or_response()) as mock_open:
            device._or_call(req)

        payload = _captured_payload(mock_open)
        assert isinstance(payload["messages"][0]["content"], str)

    def test_cache_control_only_on_system_not_user(self):
        device = _make_device([_spec("qwen/qwen3-coder", cacheable=True)])
        req = InferenceRequest(
            messages=[{"role": "user", "content": "hello"}],
            model="qwen/qwen3-coder",
            system="sys",
        )
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test-key"}), \
             patch("urllib.request.urlopen", return_value=_fake_or_response()) as mock_open:
            device._or_call(req)

        payload = _captured_payload(mock_open)
        user_msg = payload["messages"][1]
        assert user_msg["role"] == "user"
        assert user_msg["content"] == "hello"
        assert "cache_control" not in user_msg
