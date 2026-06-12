"""
model_alternatives.py — Classify model-not-found failures and suggest alternatives.

ModelAlternativesClassifier.classify(source_name, model_name, failure_exc)
  -> tuple[str, list[str]]

Returns (failure_category, alternatives) where:
  failure_category: 'model_not_found' | 'auth_error' | 'unreachable' | 'local_bug' | 'unknown'
  alternatives: ranked list of available model names from provider API
                (empty when unsupported or API unavailable)

Provider list endpoints:
  Ollama:     GET /api/tags        → model names
  Anthropic:  GET /models          → filter available, extract IDs
  OpenRouter: GET /models          → sort by featured/updated
  Google:     generativelanguage.googleapis.com /v1beta/models → model names

Fallback: when provider API fails or is unsupported, return empty alternatives.
Classification still fires — no regression on connectivity failures.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Optional

log = logging.getLogger(__name__)

# ── Failure-category detection ────────────────────────────────────────────────

_MODEL_NOT_FOUND_PHRASES = (
    "unknown model",
    "model not found",
    "no such model",
    "does not exist",
    "invalid model",
    "model_not_found",
    "not found",
)


def _is_model_not_found(exc: BaseException) -> bool:
    """Return True when exc signals a missing/unknown model."""
    msg = str(exc).lower()
    # HTTP 404 on a model endpoint
    if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
        return True
    # String patterns in error message
    return any(phrase in msg for phrase in _MODEL_NOT_FOUND_PHRASES)


def _base_category(exc: BaseException) -> str:
    """Classify non-model-not-found failures (mirrors ProviderHealthClassifier)."""
    import socket

    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return "auth_error"
        return "unknown"
    if isinstance(exc, (urllib.error.URLError, socket.timeout, OSError)):
        msg = str(exc).lower()
        if "connection refused" in msg or "name or service not known" in msg:
            return "local_bug"
        return "unreachable"
    return "unknown"


# ── Provider list fetchers ────────────────────────────────────────────────────


def _fetch_ollama_models(base_url: str) -> list[str]:
    """GET /api/tags → list of model names."""
    log.info("ModelAlternativesClassifier: fetching Ollama model list from %s/api/tags", base_url)
    url = base_url.rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        models = [m["name"] for m in data.get("models", [])]
        log.info("ModelAlternativesClassifier: Ollama returned %d models", len(models))
        return models
    except Exception as exc:
        log.info("ModelAlternativesClassifier: Ollama model list unavailable: %s", exc)
        return []


def _fetch_anthropic_models(api_key: str) -> list[str]:
    """GET https://api.anthropic.com/models → filter available, extract IDs."""
    log.info("ModelAlternativesClassifier: fetching Anthropic model list")
    url = "https://api.anthropic.com/v1/models"
    req = urllib.request.Request(
        url,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        # Response: {"data": [{"id": "...", "display_name": "...", ...}]}
        models = [m["id"] for m in data.get("data", []) if m.get("id")]
        log.info("ModelAlternativesClassifier: Anthropic returned %d models", len(models))
        return models
    except Exception as exc:
        log.info("ModelAlternativesClassifier: Anthropic model list unavailable: %s", exc)
        return []


def _fetch_openrouter_models(api_key: str) -> list[str]:
    """GET https://openrouter.ai/api/v1/models → sort by featured/updated."""
    log.info("ModelAlternativesClassifier: fetching OpenRouter model list")
    url = "https://openrouter.ai/api/v1/models"
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        # Response: {"data": [{"id": "...", "created": ..., ...}]}
        entries = data.get("data", [])
        # Sort by 'created' descending (most recent first) as provider-native ranking
        entries.sort(key=lambda m: m.get("created", 0), reverse=True)
        models = [m["id"] for m in entries if m.get("id")]
        log.info("ModelAlternativesClassifier: OpenRouter returned %d models", len(models))
        return models
    except Exception as exc:
        log.info("ModelAlternativesClassifier: OpenRouter model list unavailable: %s", exc)
        return []


def _fetch_google_models(api_key: str) -> list[str]:
    """GET generativelanguage.googleapis.com/v1beta/models → model names."""
    log.info("ModelAlternativesClassifier: fetching Google model list")
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read())
        # Response: {"models": [{"name": "models/gemini-...", "displayName": "...", ...}]}
        models = []
        for m in data.get("models", []):
            name = m.get("name", "")
            # Strip "models/" prefix to get bare model ID
            if name.startswith("models/"):
                name = name[len("models/"):]
            if name:
                models.append(name)
        log.info("ModelAlternativesClassifier: Google returned %d models", len(models))
        return models
    except Exception as exc:
        log.info("ModelAlternativesClassifier: Google model list unavailable: %s", exc)
        return []


# ── Ranking ───────────────────────────────────────────────────────────────────


def _rank_alternatives(
    models: list[str],
    requested_model: str,
    requested_context_window: Optional[int],
) -> list[str]:
    """Rank alternatives: token_window match first, then provider-native order.

    When requested_context_window is None, preserve provider-native order.
    """
    if not requested_context_window:
        return models

    # Try to look up context windows from models_registry
    try:
        from devices.inference.models_registry import default_registry
        registry = default_registry()
    except Exception:
        return models

    def _window(model_id: str) -> int:
        spec = registry.get(model_id)
        return spec.context_window if spec else 0

    target = requested_context_window

    def _sort_key(model_id: str) -> tuple:
        w = _window(model_id)
        if w == 0:
            return (1, 0)  # unknown window — after known matches
        return (0, abs(w - target))

    return sorted(models, key=_sort_key)


# ── Main classifier ───────────────────────────────────────────────────────────


class ModelAlternativesClassifier:
    """Classify model-not-found failures and fetch ranked alternatives.

    Usage::

        classifier = ModelAlternativesClassifier(source_name="ollama",
                                                  base_url="http://127.0.0.1:11434")
        category, alts = classifier.classify("ollama", "llama3:missing", exc)
    """

    def __init__(
        self,
        source_name: str,
        *,
        base_url: str = "http://127.0.0.1:11434",
        api_key: str = "",
        requested_context_window: Optional[int] = None,
    ) -> None:
        self.source_name = source_name
        self.base_url = base_url
        self.api_key = api_key
        self.requested_context_window = requested_context_window
        log.info(
            "ModelAlternativesClassifier: init source=%s base_url=%s",
            source_name,
            base_url,
        )

    def _list_models(self) -> list[str]:
        """Fetch available models from the provider API."""
        name = self.source_name
        if name == "ollama":
            return _fetch_ollama_models(self.base_url)
        if name == "anthropic":
            key = self.api_key or os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("REAL_ANTHROPIC_API_KEY", "")
            if not key:
                log.info("ModelAlternativesClassifier: no Anthropic API key — skipping model list")
                return []
            return _fetch_anthropic_models(key)
        if name == "openrouter":
            key = self.api_key or os.environ.get("OPENROUTER_API_KEY", "")
            if not key:
                log.info("ModelAlternativesClassifier: no OpenRouter API key — skipping model list")
                return []
            return _fetch_openrouter_models(key)
        if name in ("google", "google_free"):
            key = self.api_key or (
                os.environ.get("GOOGLE_AI_STUDIO_API_KEY", "")
                or os.environ.get("GOOGLE_STUDIO_API_KEY", "")
                or os.environ.get("GEMINI_API_KEY", "")
            )
            if not key:
                log.info("ModelAlternativesClassifier: no Google API key — skipping model list")
                return []
            return _fetch_google_models(key)
        log.info(
            "ModelAlternativesClassifier: unsupported provider %r — returning empty alternatives",
            name,
        )
        return []

    def classify(
        self,
        source_name: str,
        model_name: str,
        failure_exc: BaseException,
    ) -> tuple[str, list[str]]:
        """Classify failure and return (failure_category, alternatives).

        failure_category: 'model_not_found' | 'auth_error' | 'unreachable' | 'local_bug' | 'unknown'
        alternatives: ranked list of available model names (empty when unsupported/unavailable)
        """
        log.info(
            "ModelAlternativesClassifier: classify source=%s model=%s exc=%r",
            source_name,
            model_name,
            str(failure_exc)[:200],
        )

        if _is_model_not_found(failure_exc):
            raw_models = self._list_models()
            alternatives = _rank_alternatives(
                raw_models, model_name, self.requested_context_window
            )
            log.info(
                "ModelAlternativesClassifier: source=%s model=%s failure_category=model_not_found alternatives=%s",
                source_name,
                model_name,
                alternatives,
            )
            return "model_not_found", alternatives

        category = _base_category(failure_exc)
        log.info(
            "ModelAlternativesClassifier: source=%s model=%s failure_category=%s alternatives=[]",
            source_name,
            model_name,
            category,
        )
        return category, []
