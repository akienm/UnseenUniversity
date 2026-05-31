"""
test_inference_via_bus.py — T-inference-migrate-igor-to-datacenter-device Stage 1.

Verifies InferenceDevice.dispatch() for both OpenRouter and Ollama modes with
mocked HTTP. Igor's existing inference path is untouched — this tests the new
device dispatch surface only.
"""

from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from devices.inference.device import InferenceDevice, _parse_response
from devices.inference.shim import InferenceRequest, InferenceResponse
from devices.inference.sources import SourceRegistry

# ── Fixtures and helpers ──────────────────────────────────────────────────────


def _or_success(text: str = "hello", model: str = "openai/gpt-4o-mini") -> dict:
    return {
        "id": "chatcmpl-test",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _ollama_success(text: str = "hello", model: str = "llama3") -> dict:
    return {
        "model": model,
        "message": {"role": "assistant", "content": text},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 8,
        "eval_count": 4,
    }


def _mock_urlopen(response_dict: dict):
    """Patch urllib.request.urlopen to return response_dict as JSON."""
    raw = json.dumps(response_dict).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = raw
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return patch(
        "devices.inference.device.urllib.request.urlopen", return_value=mock_resp
    )


# ── InferenceRequest / InferenceResponse shapes ───────────────────────────────


class TestEnvelopeTypes:
    def test_request_defaults(self):
        req = InferenceRequest(messages=[{"role": "user", "content": "hi"}])
        assert req.model == "openai/gpt-4o-mini"
        assert req.max_tokens == 4096
        assert req.temperature == 0.0
        assert req.system == ""
        assert req.extra == {}

    def test_request_custom(self):
        req = InferenceRequest(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/claude-haiku-4-5-20251001",
            max_tokens=100,
            temperature=0.5,
            system="You are helpful.",
        )
        assert req.model == "anthropic/claude-haiku-4-5-20251001"
        assert req.max_tokens == 100
        assert req.temperature == 0.5
        assert req.system == "You are helpful."

    def test_response_defaults(self):
        resp = InferenceResponse(text="hello")
        assert resp.finish_reason == "stop"
        assert resp.input_tokens == 0
        assert resp.output_tokens == 0
        assert resp.raw == {}

    def test_response_is_dataclass(self):
        from dataclasses import fields

        names = {f.name for f in fields(InferenceRequest)}
        assert {"messages", "model", "max_tokens", "temperature", "system"} <= names


# ── _parse_response ───────────────────────────────────────────────────────────


class TestParseResponse:
    def test_parses_openai_format(self):
        raw = _or_success("result text", "openai/gpt-4o-mini")
        resp = _parse_response(raw, elapsed_ms=123)
        assert resp.text == "result text"
        assert resp.model == "openai/gpt-4o-mini"
        assert resp.finish_reason == "stop"
        assert resp.input_tokens == 10
        assert resp.output_tokens == 5
        assert resp.elapsed_ms == 123

    def test_parses_ollama_native_format(self):
        raw = _ollama_success("ollama reply", "llama3")
        resp = _parse_response(raw)
        assert resp.text == "ollama reply"
        assert resp.model == "llama3"
        assert resp.finish_reason == "stop"
        assert resp.input_tokens == 8
        assert resp.output_tokens == 4

    def test_raw_preserved(self):
        raw = _or_success("x")
        resp = _parse_response(raw)
        assert resp.raw is raw


# ── InferenceDevice.dispatch — OpenRouter mode ────────────────────────────────


class TestDispatchOpenRouter:
    def _device(self, **kwargs) -> InferenceDevice:
        kwargs.setdefault("sources", SourceRegistry())
        return InferenceDevice(mode="openrouter", **kwargs)

    def test_returns_response(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        device = self._device()
        req = InferenceRequest(messages=[{"role": "user", "content": "hello"}])
        with _mock_urlopen(_or_success("world")):
            resp = device.dispatch(req)
        assert resp.text == "world"
        assert isinstance(resp, InferenceResponse)

    def test_includes_system_message(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        device = self._device()
        req = InferenceRequest(
            messages=[{"role": "user", "content": "q"}],
            system="System instruction.",
        )
        captured = {}
        raw_open = __import__("urllib.request", fromlist=["urlopen"]).urlopen

        def capture_req(http_req, timeout=60):
            captured["body"] = json.loads(http_req.data)
            raw = json.dumps(_or_success("ok")).encode()
            mock_resp = MagicMock()
            mock_resp.read.return_value = raw
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch(
            "devices.inference.device.urllib.request.urlopen", side_effect=capture_req
        ):
            device.dispatch(req)

        msgs = captured["body"]["messages"]
        assert msgs[0]["role"] == "system"
        assert msgs[0]["content"] == "System instruction."

    def test_raises_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        device = self._device()
        req = InferenceRequest(messages=[{"role": "user", "content": "x"}])
        with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
            device.dispatch(req)

    def test_raises_on_http_error(self, monkeypatch):
        import urllib.error

        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        device = self._device()
        req = InferenceRequest(messages=[{"role": "user", "content": "x"}])
        err = urllib.error.HTTPError(
            url="https://openrouter.ai",
            code=429,
            msg="Rate limited",
            hdrs=None,
            fp=BytesIO(b"rate limited"),
        )
        with patch("devices.inference.device.urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError, match="429"):
                device.dispatch(req)

    def test_blocked_device_raises(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        device = self._device()
        device.block("maintenance")
        req = InferenceRequest(messages=[{"role": "user", "content": "x"}])
        with pytest.raises(RuntimeError, match="blocked"):
            device.dispatch(req)


# ── InferenceDevice.dispatch — Ollama mode ────────────────────────────────────


class TestDispatchOllama:
    def _device(self) -> InferenceDevice:
        return InferenceDevice(
            mode="ollama", endpoint="http://127.0.0.1:11434", sources=SourceRegistry()
        )

    def test_returns_response(self):
        device = self._device()
        req = InferenceRequest(
            messages=[{"role": "user", "content": "ping"}], model="llama3"
        )
        with _mock_urlopen(_ollama_success("pong", "llama3")):
            resp = device.dispatch(req)
        assert resp.text == "pong"
        assert resp.model == "llama3"

    def test_uses_ollama_chat_endpoint(self):
        device = self._device()
        req = InferenceRequest(
            messages=[{"role": "user", "content": "x"}], model="llama3"
        )
        captured = {}

        def capture_req(http_req, timeout=60):
            captured["url"] = http_req.full_url
            raw = json.dumps(_ollama_success("y")).encode()
            mock_resp = MagicMock()
            mock_resp.read.return_value = raw
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch(
            "devices.inference.device.urllib.request.urlopen", side_effect=capture_req
        ):
            device.dispatch(req)

        assert "/api/chat" in captured["url"]

    def test_raises_on_http_error(self):
        import urllib.error

        device = self._device()
        req = InferenceRequest(messages=[{"role": "user", "content": "x"}])
        err = urllib.error.HTTPError(
            url="http://127.0.0.1:11434",
            code=500,
            msg="Server error",
            hdrs=None,
            fp=BytesIO(b"internal error"),
        )
        with patch("devices.inference.device.urllib.request.urlopen", side_effect=err):
            with pytest.raises(RuntimeError, match="500"):
                device.dispatch(req)

    def test_extra_fields_passed_through(self):
        device = self._device()
        req = InferenceRequest(
            messages=[{"role": "user", "content": "x"}],
            model="llama3",
            extra={"keep_alive": "5m"},
        )
        captured = {}

        def capture_req(http_req, timeout=60):
            captured["body"] = json.loads(http_req.data)
            raw = json.dumps(_ollama_success("y")).encode()
            mock_resp = MagicMock()
            mock_resp.read.return_value = raw
            mock_resp.__enter__ = lambda s: s
            mock_resp.__exit__ = MagicMock(return_value=False)
            return mock_resp

        with patch(
            "devices.inference.device.urllib.request.urlopen", side_effect=capture_req
        ):
            device.dispatch(req)

        assert captured["body"].get("keep_alive") == "5m"
