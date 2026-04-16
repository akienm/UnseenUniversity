"""
NetworkProxy — unified outbound HTTP with per-host health view.

Problem: budget.py, embedder, cluster_router, OR reasoner, discord each roll
their own timeout and retry. No aggregate view of external call health.

Design (T-network-proxy):
  - http_get(url, headers, timeout) → bytes | None
  - http_post(url, data, headers, timeout) → bytes | None
  - Per-host HostStats: call_count, error_count, latency samples (p50/p95)
  - report_str() → formatted string for /audit and get_network_proxy_report tool

Callers opt-in — existing urlopen sites migrate over time.
No retry logic in v1 (each caller decides its own retry policy).
"""

import bisect
import json
import time
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional
from ..igor_base import IgorBase

# ── Per-host stats ─────────────────────────────────────────────────────────────


@dataclass
class HostStats:
    """Call statistics for a single hostname. Thread-safe via external lock."""

    host: str
    call_count: int = 0
    error_count: int = 0
    _samples: list = field(default_factory=list)  # sorted latency_ms ints

    _MAX_SAMPLES: int = 500

    def record(self, elapsed_ms: int, success: bool) -> None:
        self.call_count += 1
        if not success:
            self.error_count += 1
        bisect.insort(self._samples, elapsed_ms)
        if len(self._samples) > self._MAX_SAMPLES:
            self._samples.pop(0)

    @property
    def error_rate(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.error_count / self.call_count

    def _pct(self, p: int) -> Optional[int]:
        if not self._samples:
            return None
        idx = max(0, int(len(self._samples) * p / 100) - 1)
        return self._samples[idx]

    @property
    def p50(self) -> Optional[int]:
        return self._pct(50)

    @property
    def p95(self) -> Optional[int]:
        return self._pct(95)

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "calls": self.call_count,
            "errors": self.error_count,
            "error_rate": round(self.error_rate, 4),
            "p50_ms": self.p50,
            "p95_ms": self.p95,
        }


# ── NetworkProxy ───────────────────────────────────────────────────────────────


def _extract_host(url: str) -> str:
    """Return hostname from URL for grouping stats. Falls back to full URL."""
    try:
        # Simple extraction: strip scheme, take up to first / or :
        after_scheme = url.split("://", 1)[-1]
        host_part = after_scheme.split("/")[0].split(":")[0]
        return host_part or url
    except Exception:
        return url


class NetworkProxy(IgorBase):
    """
    Single outbound HTTP wrapper. Tracks per-host call counts, failures, latency.
    Singleton via module-level `proxy`.

    Usage:
        from igor.network.proxy import proxy
        data = proxy.post(url, payload_bytes, headers={"Authorization": "..."})
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._hosts: dict[str, HostStats] = {}

    def _stats_for(self, host: str) -> HostStats:
        if host not in self._hosts:
            self._hosts[host] = HostStats(host=host)
        return self._hosts[host]

    def get(
        self,
        url: str,
        headers: Optional[dict] = None,
        timeout: float = 10.0,
    ) -> Optional[bytes]:
        """
        HTTP GET. Returns response body bytes, or None on any error.
        Callers that need to distinguish errors should use urlopen directly.
        """
        return self._call(url, data=None, headers=headers or {}, timeout=timeout)

    def post(
        self,
        url: str,
        data: bytes,
        headers: Optional[dict] = None,
        timeout: float = 15.0,
    ) -> Optional[bytes]:
        """HTTP POST with bytes body. Returns response body or None on error."""
        return self._call(url, data=data, headers=headers or {}, timeout=timeout)

    def post_json(
        self,
        url: str,
        payload: dict,
        headers: Optional[dict] = None,
        timeout: float = 15.0,
    ) -> Optional[dict]:
        """
        HTTP POST with JSON-serialized payload. Returns parsed JSON dict or None.
        Merges Content-Type: application/json into headers automatically.
        """
        merged = {"Content-Type": "application/json"}
        if headers:
            merged.update(headers)
        body = json.dumps(payload).encode()
        resp_bytes = self._call(url, data=body, headers=merged, timeout=timeout)
        if resp_bytes is None:
            return None
        try:
            return json.loads(resp_bytes)
        except Exception:
            return None

    def _call(
        self,
        url: str,
        data: Optional[bytes],
        headers: dict,
        timeout: float,
    ) -> Optional[bytes]:
        host = _extract_host(url)
        req = urllib.request.Request(url, data=data, headers=headers)
        t0 = time.perf_counter()
        success = False
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            success = True
            return body
        except Exception:
            return None
        finally:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            with self._lock:
                self._stats_for(host).record(elapsed_ms, success)

    def host_stats(self) -> list[dict]:
        """Return per-host stats sorted by call_count descending."""
        with self._lock:
            return sorted(
                [h.to_dict() for h in self._hosts.values()],
                key=lambda d: d["calls"],
                reverse=True,
            )

    def report_str(self) -> str:
        """Formatted report for /audit and get_network_proxy_report tool."""
        stats = self.host_stats()
        if not stats:
            return "NETWORK PROXY — no outbound calls recorded."
        lines = [f"NETWORK PROXY — {len(stats)} host(s):\n"]
        for h in stats:
            p50 = f"{h['p50_ms']}ms" if h["p50_ms"] is not None else "—"
            p95 = f"{h['p95_ms']}ms" if h["p95_ms"] is not None else "—"
            err_pct = f"{h['error_rate']:.0%}" if h["errors"] > 0 else "0%"
            lines.append(
                f"  {h['host']:<36}  {h['calls']:4}x  err={err_pct}"
                f"  p50={p50}  p95={p95}"
            )
        return "\n".join(lines)


# Module-level singleton
proxy = NetworkProxy()
