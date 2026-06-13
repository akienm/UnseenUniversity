"""
provider_health.py — Classify ping failures to distinguish local issues from real outages.

ProviderHealthClassifier.classify(source_name, failure_exc) -> str

Returns one of:
  local_bug       — socket.gaierror (DNS), connection refused, other local network issues
  auth_error      — HTTP 401/403, invalid API key
  unreachable     — timeout, connection refused by remote, other connectivity issues
  unknown         — anything else

This prevents misrouting flat-rate sources (Ollama) to expensive ones (OpenRouter)
when the actual failure is our DNS/auth, not a real provider outage.

Injected into Source.ping() exception blocks via _classify_ping_failure_simple().
"""

from __future__ import annotations

import socket
import urllib.error


class ProviderHealthClassifier:
    """Classify provider ping failures."""

    @staticmethod
    def classify(source_name: str, exc: BaseException) -> str:
        """Classify a ping failure exception.

        Args:
            source_name: provider name (for logging context)
            exc: the exception raised by ping()

        Returns:
            str: one of 'local_bug', 'auth_error', 'unreachable', 'unknown'
        """
        if isinstance(exc, urllib.error.HTTPError):
            if exc.code in (401, 403):
                return "auth_error"
            return "unknown"

        msg = str(exc).lower()
        if isinstance(exc, (urllib.error.URLError, socket.timeout, OSError)):
            if "connection refused" in msg or "name or service not known" in msg:
                return "local_bug"
            return "unreachable"

        return "unknown"
