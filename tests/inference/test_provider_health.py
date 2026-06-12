"""Test ProviderHealthClassifier and Source._classify_ping_failure_simple()."""

import socket
import urllib.error
from unittest.mock import Mock, patch

from devices.inference.provider_health import ProviderHealthClassifier
from devices.inference.sources import Source


def test_classify_local_bug_dns():
    """DNS resolution failure → local_bug."""
    exc = socket.gaierror("Name or service not known")
    category = ProviderHealthClassifier.classify("test_source", exc)
    assert category == "local_bug"


def test_classify_local_bug_connection_refused():
    """Connection refused → local_bug."""
    exc = ConnectionRefusedError("Connection refused")
    category = ProviderHealthClassifier.classify("test_source", exc)
    assert category == "local_bug"


def test_classify_auth_error_401():
    """HTTP 401 → auth_error."""
    exc = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
    category = ProviderHealthClassifier.classify("test_source", exc)
    assert category == "auth_error"


def test_classify_auth_error_403():
    """HTTP 403 → auth_error."""
    exc = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
    category = ProviderHealthClassifier.classify("test_source", exc)
    assert category == "auth_error"


def test_classify_unreachable_timeout():
    """Socket timeout → unreachable."""
    exc = socket.timeout("timed out")
    category = ProviderHealthClassifier.classify("test_source", exc)
    assert category == "unreachable"


def test_classify_unreachable_oserror():
    """Generic OSError → unreachable."""
    exc = OSError("Network is unreachable")
    category = ProviderHealthClassifier.classify("test_source", exc)
    assert category == "unreachable"


def test_classify_unknown():
    """Unknown exception → unknown."""
    exc = ValueError("unknown error")
    category = ProviderHealthClassifier.classify("test_source", exc)
    assert category == "unknown"


def test_source_classify_ping_failure_logs(caplog):
    """Source._classify_ping_failure_simple logs at INFO."""
    src = Source(name="test_source")
    exc = socket.gaierror("Name or service not known")
    with caplog.at_level("INFO"):
        category = src._classify_ping_failure_simple(exc)
    assert category == "local_bug"
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "INFO"
    assert "failure_category=local_bug" in caplog.text


def test_openrouter_ping_failure_classified(caplog):
    """OpenRouterSource.ping() classifies failures."""
    from devices.inference.sources import OpenRouterSource
    src = OpenRouterSource()
    with patch("socket.create_connection", side_effect=socket.gaierror("DNS failure")):
        with caplog.at_level("INFO"):
            result = src.ping()
    assert result is False
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "INFO"
    assert "failure_category=local_bug" in caplog.text


def test_ollama_ping_failure_classified(caplog):
    """OllamaSource.ping() classifies failures."""
    from devices.inference.sources import OllamaSource
    src = OllamaSource()
    with patch("socket.create_connection", side_effect=ConnectionRefusedError("refused")):
        with caplog.at_level("INFO"):
            result = src.ping()
    assert result is False
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "INFO"
    assert "failure_category=local_bug" in caplog.text
