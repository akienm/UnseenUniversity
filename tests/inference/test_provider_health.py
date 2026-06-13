"""
test_provider_health.py — Tests for ProviderHealthClassifier
"""

from __future__ import annotations

import socket
import urllib.error
from unittest.mock import Mock

import pytest

from devices.inference.provider_health import ProviderHealthClassifier


class TestProviderHealthClassifier:
    """Tests for classification of provider ping failures."""

    def test_auth_error_401(self):
        """HTTP 401 should be classified as auth_error."""
        exc = urllib.error.HTTPError("http://example.com", 401, "Unauthorized", {}, None)
        result = ProviderHealthClassifier.classify("openrouter", exc)
        assert result == "auth_error"

    def test_auth_error_403(self):
        """HTTP 403 should be classified as auth_error."""
        exc = urllib.error.HTTPError("http://example.com", 403, "Forbidden", {}, None)
        result = ProviderHealthClassifier.classify("ollama", exc)
        assert result == "auth_error"

    def test_http_error_other_code(self):
        """HTTP errors other than 401/403 should be classified as unknown."""
        exc = urllib.error.HTTPError("http://example.com", 500, "Server Error", {}, None)
        result = ProviderHealthClassifier.classify("openrouter", exc)
        assert result == "unknown"

    def test_dns_error(self):
        """DNS failure (gaierror) should be classified as local_bug."""
        exc = socket.gaierror(-2, "Name or service not known")
        result = ProviderHealthClassifier.classify("google", exc)
        assert result == "local_bug"

    def test_connection_refused(self):
        """Connection refused should be classified as local_bug."""
        exc = OSError("Connection refused")
        result = ProviderHealthClassifier.classify("ollama", exc)
        assert result == "local_bug"

    def test_timeout(self):
        """Socket timeout should be classified as unreachable."""
        exc = socket.timeout("Connection timed out")
        result = ProviderHealthClassifier.classify("anthropic", exc)
        assert result == "unreachable"

    def test_url_error(self):
        """URLError (non-specific) should be classified as unreachable."""
        exc = urllib.error.URLError("Connection reset by peer")
        result = ProviderHealthClassifier.classify("openrouter", exc)
        assert result == "unreachable"

    def test_generic_oserror(self):
        """Generic OSError should be classified as unreachable."""
        exc = OSError("Some OS error")
        result = ProviderHealthClassifier.classify("ollama", exc)
        assert result == "unreachable"

    def test_unknown_exception(self):
        """Unknown exception types should be classified as unknown."""
        exc = ValueError("Some value error")
        result = ProviderHealthClassifier.classify("custom", exc)
        assert result == "unknown"

    def test_source_name_unused(self):
        """source_name parameter doesn't affect classification."""
        exc = urllib.error.HTTPError("http://example.com", 401, "Unauthorized", {}, None)
        result1 = ProviderHealthClassifier.classify("openrouter", exc)
        result2 = ProviderHealthClassifier.classify("different_source", exc)
        assert result1 == result2 == "auth_error"
