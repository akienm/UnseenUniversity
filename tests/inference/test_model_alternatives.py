"""
Tests for devices/inference/model_alternatives.py.

Tests:
- _is_model_not_found: HTTP 404 detected
- _is_model_not_found: 'unknown model' phrase detected
- _is_model_not_found: generic exception without patterns returns False
- _base_category: auth_error on HTTP 401/403
- _base_category: unreachable on URLError
- _base_category: local_bug on connection refused
- _fetch_ollama_models: returns model names from /api/tags
- _fetch_ollama_models: returns empty on network error (fail-open)
- _fetch_anthropic_models: returns IDs from /v1/models response
- _fetch_anthropic_models: returns empty when no API key (handled in classifier)
- _fetch_openrouter_models: returns IDs sorted by created descending
- _fetch_openrouter_models: returns empty on network error
- _fetch_google_models: strips 'models/' prefix
- _fetch_google_models: returns empty on network error
- _rank_alternatives: preserves provider order when no context window
- _rank_alternatives: sorts by context-window proximity when window given
- ModelAlternativesClassifier.classify: model_not_found triggers list fetch + log
- ModelAlternativesClassifier.classify: non-model failure returns category with empty list
- ModelAlternativesClassifier.classify: unsupported provider returns empty alternatives
- Source._classify_ping_failure: model_not_found routed correctly + log line emitted
"""

from __future__ import annotations

import json
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from unseen_university.devices.inference.model_alternatives import (
    ModelAlternativesClassifier,
    _base_category,
    _fetch_anthropic_models,
    _fetch_google_models,
    _fetch_ollama_models,
    _fetch_openrouter_models,
    _is_model_not_found,
    _rank_alternatives,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _http_error(code: int, url: str = "http://test/") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "err", {}, None)


def _mock_urlopen(response_body: bytes):
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = response_body
    return resp


# ── _is_model_not_found ───────────────────────────────────────────────────────

def test_is_model_not_found_http_404():
    exc = _http_error(404)
    assert _is_model_not_found(exc) is True


def test_is_model_not_found_unknown_model_phrase():
    exc = RuntimeError("Unknown model 'llama3:bad'")
    assert _is_model_not_found(exc) is True


def test_is_model_not_found_not_found_phrase():
    exc = ValueError("model not found in registry")
    assert _is_model_not_found(exc) is True


def test_is_model_not_found_returns_false_for_generic_error():
    exc = ConnectionError("connection timeout")
    assert _is_model_not_found(exc) is False


def test_is_model_not_found_http_500_is_false():
    exc = _http_error(500)
    assert _is_model_not_found(exc) is False


# ── _base_category ────────────────────────────────────────────────────────────

def test_base_category_401_is_auth_error():
    assert _base_category(_http_error(401)) == "auth_error"


def test_base_category_403_is_auth_error():
    assert _base_category(_http_error(403)) == "auth_error"


def test_base_category_url_error_is_unreachable():
    exc = urllib.error.URLError("timed out")
    assert _base_category(exc) == "unreachable"


def test_base_category_connection_refused_is_local_bug():
    exc = urllib.error.URLError("connection refused")
    assert _base_category(exc) == "local_bug"


def test_base_category_unknown_returns_unknown():
    exc = RuntimeError("some other error")
    assert _base_category(exc) == "unknown"


# ── _fetch_ollama_models ──────────────────────────────────────────────────────

def test_fetch_ollama_models_extracts_names():
    payload = json.dumps({"models": [{"name": "llama3"}, {"name": "mistral"}]}).encode()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
        result = _fetch_ollama_models("http://127.0.0.1:11434")
    assert result == ["llama3", "mistral"]


def test_fetch_ollama_models_returns_empty_on_network_error():
    with patch("urllib.request.urlopen", side_effect=OSError("refused")):
        result = _fetch_ollama_models("http://127.0.0.1:11434")
    assert result == []


# ── _fetch_anthropic_models ───────────────────────────────────────────────────

def test_fetch_anthropic_models_extracts_ids():
    payload = json.dumps({"data": [{"id": "claude-3-opus"}, {"id": "claude-3-sonnet"}]}).encode()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
        result = _fetch_anthropic_models("sk-ant-test")
    assert "claude-3-opus" in result
    assert "claude-3-sonnet" in result


def test_fetch_anthropic_models_returns_empty_on_error():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
        result = _fetch_anthropic_models("sk-ant-test")
    assert result == []


# ── _fetch_openrouter_models ──────────────────────────────────────────────────

def test_fetch_openrouter_models_sorted_by_created_descending():
    payload = json.dumps({"data": [
        {"id": "older-model", "created": 1000},
        {"id": "newer-model", "created": 9000},
    ]}).encode()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
        result = _fetch_openrouter_models("or-key")
    assert result[0] == "newer-model"
    assert result[1] == "older-model"


def test_fetch_openrouter_models_returns_empty_on_error():
    with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
        result = _fetch_openrouter_models("or-key")
    assert result == []


# ── _fetch_google_models ──────────────────────────────────────────────────────

def test_fetch_google_models_strips_models_prefix():
    payload = json.dumps({"models": [
        {"name": "models/gemini-1.5-flash"},
        {"name": "models/gemini-pro"},
    ]}).encode()
    with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
        result = _fetch_google_models("goog-key")
    assert "gemini-1.5-flash" in result
    assert "gemini-pro" in result
    assert not any("models/" in m for m in result)


def test_fetch_google_models_returns_empty_on_error():
    with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
        result = _fetch_google_models("goog-key")
    assert result == []


# ── _rank_alternatives ────────────────────────────────────────────────────────

def test_rank_alternatives_preserves_order_without_context_window():
    models = ["z-model", "a-model", "m-model"]
    ranked = _rank_alternatives(models, "requested", None)
    assert ranked == ["z-model", "a-model", "m-model"]


def test_rank_alternatives_empty_context_window_preserves_order():
    models = ["first", "second"]
    ranked = _rank_alternatives(models, "requested", 0)
    assert ranked == ["first", "second"]


# ── ModelAlternativesClassifier.classify ──────────────────────────────────────

def test_classify_model_not_found_fetches_alternatives(caplog):
    import logging
    exc = _http_error(404)
    classifier = ModelAlternativesClassifier(
        source_name="ollama", base_url="http://127.0.0.1:11434"
    )
    payload = json.dumps({"models": [{"name": "llama3"}, {"name": "mistral"}]}).encode()

    with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)), \
         caplog.at_level(logging.INFO, logger="unseen_university.devices.inference.model_alternatives"):
        category, alts = classifier.classify("ollama", "llama3:missing", exc)

    assert category == "model_not_found"
    assert "llama3" in alts
    assert "failure_category=model_not_found" in caplog.text


def test_classify_non_model_failure_returns_empty_alternatives():
    exc = urllib.error.HTTPError("http://test/", 401, "Unauthorized", {}, None)
    classifier = ModelAlternativesClassifier(source_name="anthropic")

    category, alts = classifier.classify("anthropic", "claude-3", exc)

    assert category == "auth_error"
    assert alts == []


def test_classify_unsupported_provider_returns_empty():
    exc = _http_error(404)
    classifier = ModelAlternativesClassifier(source_name="unknown-provider")

    category, alts = classifier.classify("unknown-provider", "some-model", exc)

    assert category == "model_not_found"
    assert alts == []  # no fetcher for unknown provider


def test_classify_network_failure_returns_unknown():
    exc = RuntimeError("network down")
    classifier = ModelAlternativesClassifier(source_name="openrouter")

    category, alts = classifier.classify("openrouter", "some-model", exc)

    assert category == "unknown"
    assert alts == []


# ── Source._classify_ping_failure integration ─────────────────────────────────

def test_source_classify_ping_failure_emits_log_on_model_not_found(caplog):
    """Source._classify_ping_failure logs 'source X model Y not found: alternatives=...' for 404."""
    import logging
    from unseen_university.devices.inference.sources import Source

    class _TestSource(Source):
        def __init__(self):
            super().__init__(name="ollama")

        def ping(self) -> bool:
            return False

    src = _TestSource()
    exc = _http_error(404)
    payload = json.dumps({"models": [{"name": "available-model"}]}).encode()

    with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)), \
         caplog.at_level(logging.INFO, logger="unseen_university.devices.inference.sources"):
        result = src._classify_ping_failure(
            exc, model_name="missing-model", base_url="http://127.0.0.1:11434"
        )

    assert result["failure_category"] == "model_not_found"
    assert "not found" in caplog.text
    assert "missing-model" in caplog.text


def test_source_classify_ping_failure_non_model_error():
    from unseen_university.devices.inference.sources import Source

    class _TestSource(Source):
        def __init__(self):
            super().__init__(name="openrouter")

        def ping(self) -> bool:
            return False

    src = _TestSource()
    exc = urllib.error.HTTPError("http://test/", 403, "Forbidden", {}, None)
    result = src._classify_ping_failure(exc, model_name="some-model")

    assert result["failure_category"] == "auth_error"
    assert result["alternatives"] == []
