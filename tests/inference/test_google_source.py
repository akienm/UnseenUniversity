"""Tests for GoogleSource, updated models registry, and designer cascade routing."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest


# ── GoogleSource: message format conversion ───────────────────────────────────


class TestGoogleSourceMessageConversion:
    def _source(self, free_tier=False):
        from devices.inference.sources import GoogleSource
        return GoogleSource(free_tier=free_tier)

    def _req(self, messages, system="", model="gemini-2.0-flash"):
        from devices.inference.shim import InferenceRequest
        return InferenceRequest(model=model, messages=messages, system=system)

    def test_user_message_converts(self):
        src = self._source()
        req = self._req([{"role": "user", "content": "Hello"}])
        contents, sys_inst = src._to_google_messages(req)
        assert contents == [{"role": "user", "parts": [{"text": "Hello"}]}]
        assert sys_inst is None

    def test_assistant_becomes_model_role(self):
        src = self._source()
        req = self._req([
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hey"},
        ])
        contents, _ = src._to_google_messages(req)
        assert contents[1]["role"] == "model"

    def test_system_prompt_extracted_from_req(self):
        src = self._source()
        req = self._req([{"role": "user", "content": "Q?"}], system="Be concise.")
        contents, sys_inst = src._to_google_messages(req)
        assert sys_inst is not None
        assert "concise" in sys_inst["parts"][0]["text"]
        assert all(m["role"] != "system" for m in contents)

    def test_inline_system_message_extracted(self):
        src = self._source()
        req = self._req([
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "Explain gravity."},
        ])
        contents, sys_inst = src._to_google_messages(req)
        assert len(contents) == 1
        assert sys_inst["parts"][0]["text"] == "Be brief."

    def test_model_name_strips_google_prefix(self):
        src = self._source()
        assert src._model_name("google/gemini-2.0-flash") == "gemini-2.0-flash"
        assert src._model_name("gemini-2.0-flash") == "gemini-2.0-flash"

    def test_free_tier_name(self):
        from devices.inference.sources import GoogleSource
        assert GoogleSource(free_tier=True).name == "google_free"
        assert GoogleSource(free_tier=False).name == "google"


# ── GoogleSource: API key resolution ──────────────────────────────────────────


class TestGoogleSourceApiKey:
    def test_missing_key_raises(self, monkeypatch):
        from devices.inference.sources import GoogleSource
        monkeypatch.delenv("GOOGLE_AI_STUDIO_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        src = GoogleSource()
        with pytest.raises(RuntimeError, match="Google API key not set"):
            src._api_key()

    def test_primary_key_used(self, monkeypatch):
        from devices.inference.sources import GoogleSource
        monkeypatch.setenv("GOOGLE_AI_STUDIO_API_KEY", "primary-key")
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert GoogleSource()._api_key() == "primary-key"

    def test_alias_key_used_when_primary_absent(self, monkeypatch):
        from devices.inference.sources import GoogleSource
        monkeypatch.delenv("GOOGLE_AI_STUDIO_API_KEY", raising=False)
        monkeypatch.setenv("GEMINI_API_KEY", "alias-key")
        assert GoogleSource()._api_key() == "alias-key"

    def test_primary_takes_precedence_over_alias(self, monkeypatch):
        from devices.inference.sources import GoogleSource
        monkeypatch.setenv("GOOGLE_AI_STUDIO_API_KEY", "primary")
        monkeypatch.setenv("GEMINI_API_KEY", "alias")
        assert GoogleSource()._api_key() == "primary"


# ── GoogleSource: API call + response normalization ───────────────────────────


def _fake_google_response(text="ok", cached_tokens=0):
    return json.dumps({
        "candidates": [
            {"content": {"parts": [{"text": text}], "role": "model"}, "finishReason": "STOP"}
        ],
        "usageMetadata": {
            "promptTokenCount": 20,
            "candidatesTokenCount": 5,
            "cachedContentTokenCount": cached_tokens,
        },
    }).encode()


def _patch_urlopen(resp_bytes):
    mock_resp = MagicMock()
    mock_resp.read.return_value = resp_bytes
    mock_ctx = MagicMock()
    mock_ctx.__enter__.return_value = mock_resp
    mock_ctx.__exit__.return_value = False
    return patch("urllib.request.urlopen", return_value=mock_ctx)


class TestGoogleSourceCall:
    def _req(self, content="Hello", model="gemini-2.0-flash"):
        from devices.inference.shim import InferenceRequest
        return InferenceRequest(model=model, messages=[{"role": "user", "content": content}])

    def test_returns_normalized_response(self, monkeypatch):
        from devices.inference.sources import GoogleSource
        monkeypatch.setenv("GOOGLE_AI_STUDIO_API_KEY", "test-key")
        src = GoogleSource()
        with _patch_urlopen(_fake_google_response("Hello back")):
            result = src.call(self._req())
        assert result["choices"][0]["message"]["content"] == "Hello back"
        assert result["usage"]["prompt_tokens"] == 20

    def test_url_contains_model_and_key(self, monkeypatch):
        from devices.inference.sources import GoogleSource
        monkeypatch.setenv("GOOGLE_AI_STUDIO_API_KEY", "mykey123")
        src = GoogleSource()
        captured = []

        def _fake_urlopen(req, timeout=None):
            captured.append(req.full_url)
            mock_resp = MagicMock()
            mock_resp.read.return_value = _fake_google_response()
            mock_ctx = MagicMock()
            mock_ctx.__enter__.return_value = mock_resp
            return mock_ctx

        with patch("urllib.request.urlopen", _fake_urlopen):
            src.call(self._req(model="gemini-2.0-flash"))

        assert captured
        url = captured[0]
        assert "gemini-2.0-flash:generateContent" in url
        assert "key=mykey123" in url

    def test_cached_tokens_logged_in_usage(self, monkeypatch):
        from devices.inference.sources import GoogleSource
        monkeypatch.setenv("GOOGLE_AI_STUDIO_API_KEY", "key")
        src = GoogleSource()
        with _patch_urlopen(_fake_google_response("answer", cached_tokens=8000)):
            result = src.call(self._req())
        assert result["usage"]["cached_content_token_count"] == 8000


# ── Models registry: Google native models ────────────────────────────────────


class TestModelsRegistryGoogleModels:
    def test_google_free_model_present(self):
        from devices.inference.models_registry import default_registry
        spec = default_registry().get("gemini-2.0-flash")
        assert spec is not None
        assert spec.source_name == "google_free"
        assert spec.input_cost_per_1m == 0.0
        assert "free-tier" in spec.tags

    def test_google_paid_model_present_and_cacheable(self):
        from devices.inference.models_registry import default_registry
        spec = default_registry().get("gemini-2.0-flash-paid")
        assert spec is not None
        assert spec.source_name == "google"
        assert spec.cacheable

    def test_or_gemini_still_present_as_fallback(self):
        from devices.inference.models_registry import default_registry
        spec = default_registry().get("google/gemini-2.0-flash")
        assert spec is not None
        assert spec.source_name == "openrouter"
        assert "or-fallback" in spec.tags


# ── Rules engine: designer cascade ───────────────────────────────────────────


def _make_engine(available_source_names):
    from devices.inference.models_registry import default_registry
    from devices.inference.rules_engine import RulesEngine
    from devices.inference.sources import Source, SourceRegistry

    sreg = SourceRegistry()
    for name in available_source_names:
        s = Source(name=name)
        s.available = True
        sreg.register(s)

    return RulesEngine(sources=sreg, models=default_registry())


class TestDesignerCascade:
    def test_prefers_google_free_first(self):
        e = _make_engine(["google_free", "google", "openrouter", "anthropic"])
        d = e.route("designer")
        assert d is not None
        assert d.source.name == "google_free"

    def test_falls_to_google_paid_when_free_gone(self):
        e = _make_engine(["google", "openrouter", "anthropic"])
        d = e.route("designer")
        assert d is not None
        assert d.source.name == "google"

    def test_falls_to_openrouter_when_google_gone(self):
        e = _make_engine(["openrouter", "anthropic"])
        d = e.route("designer")
        assert d is not None
        assert d.source.name == "openrouter"

    def test_falls_to_anthropic_as_last_resort(self):
        e = _make_engine(["anthropic"])
        d = e.route("designer")
        assert d is not None
        assert d.source.name == "anthropic"

    def test_no_available_source_returns_none(self):
        e = _make_engine([])
        d = e.route("designer")
        assert d is None


# ── Source registry: Google sources present ──────────────────────────────────


class TestDefaultSourceRegistry:
    def test_google_free_registered(self):
        from devices.inference.sources import default_registry
        assert default_registry().get("google_free") is not None

    def test_google_paid_registered(self):
        from devices.inference.sources import default_registry
        assert default_registry().get("google") is not None

    def test_anthropic_has_caching_header(self):
        from devices.inference.sources import AnthropicSource
        assert "prompt-caching" in AnthropicSource().BETA_HEADERS
