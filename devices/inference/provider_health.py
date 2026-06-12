"""provider_health.py — classify ping failures for observability.

ProviderHealthClassifier categorizes exceptions from source ping() calls
so we can distinguish our bugs (DNS, auth) from real provider outages.
"""

from __future__ import annotations
import socket
import urllib.error
from typing import Literal

FailureCategory = Literal["local_bug", "auth_error", "unreachable", "unknown"]


class ProviderHealthClassifier:
    """Classify a ping failure by exception type.

    Returns one of:
      local_bug — socket.gaierror/DNS, connection refused
      auth_error — HTTPError 401/403
      unreachable — timeout, other connection errors
      unknown — anything else
    """

    @staticmethod
    def classify(source_name: str, failure_exc: BaseException) -> FailureCategory:
        """Classify the exception and return a category.

        Args:
            source_name: name of the source (e.g., 'openrouter')
            failure_exc: exception raised during ping

        Returns:
            One of: 'local_bug', 'auth_error', 'unreachable', 'unknown'
        """
        if isinstance(failure_exc, socket.gaierror):
            # DNS resolution failure — our local bug
            return "local_bug"
        if isinstance(failure_exc, ConnectionRefusedError):
            # Connection refused — our local bug (wrong port/address)
            return "local_bug"
        if isinstance(failure_exc, urllib.error.HTTPError):
            code = failure_exc.code
            if code in (401, 403):
                return "auth_error"
            # Other HTTP errors (500, 503, etc.) are treated as unreachable
            return "unreachable"
        if isinstance(failure_exc, socket.timeout):
            return "unreachable"
        if isinstance(failure_exc, OSError):
            # Generic OSError (e.g., network unreachable) — treat as unreachable
            return "unreachable"
        # Fallback for any other exception type
        return "unknown"
