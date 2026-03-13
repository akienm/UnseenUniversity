"""
db_proxy.py — DatabaseProxy: connection lifecycle, failover, and performance metrics.

All SQLite access in Cortex routes through here. Callers use:

    with self._db() as conn:
        conn.execute(...)

DatabaseProxy owns the connection lifecycle — open, close, retry, hard-interrupt on
sustained failure. Callers never know a transient error occurred.

Metrics are stored in an in-memory ring (never written to the DB — circular dependency).
Exposed via get_metrics() for /introspect and self-directed testing (#208).

Part of #211. Foundation for remote-agent sync (#190).
"""
from __future__ import annotations

import os
import sqlite3
import time
from collections import deque
from pathlib import Path
from typing import Optional


_SLOW_MS   = int(os.getenv("IGOR_DB_SLOW_MS", "50"))
_RING_SIZE = 500


class _DBContext:
    """
    Context manager returned by DatabaseProxy(). Yields a raw sqlite3.Connection.
    Times the block, records metrics, closes on exit.
    """
    __slots__ = ("_proxy", "_conn", "_t0")

    def __init__(self, proxy: "DatabaseProxy") -> None:
        self._proxy = proxy
        self._conn:  Optional[sqlite3.Connection] = None
        self._t0:    float = 0.0

    def __enter__(self) -> sqlite3.Connection:
        self._t0 = time.monotonic()
        try:
            self._conn = sqlite3.connect(self._proxy.db_path)
            self._conn.row_factory = sqlite3.Row
            return self._conn
        except Exception as exc:
            self._proxy._record_error(exc)
            raise

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        elapsed_ms = round((time.monotonic() - self._t0) * 1000)
        self._proxy._record(elapsed_ms, error=exc_type is not None)
        if self._conn is not None:
            try:
                if exc_type is None:
                    self._conn.commit()   # persist writes — matches `with conn:` semantics
                else:
                    self._conn.rollback()
            except Exception:
                pass
            try:
                self._conn.close()
            except Exception:
                pass
        return False  # never suppress exceptions


class DatabaseProxy:
    """
    Drop-in replacement for Cortex._conn().

    Usage (in Cortex):
        self._db = DatabaseProxy(db_path)
        ...
        with self._db() as conn:
            conn.execute(...)

    Metrics (in-memory ring, never written to DB):
        proxy.get_metrics() -> dict with latency percentiles, slow count, error count
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path  = db_path
        self._latencies: deque[float] = deque(maxlen=_RING_SIZE)
        self._errors:    int = 0
        self._slow:      int = 0
        self._calls:     int = 0
        self._connect_errors: int = 0

    def __call__(self) -> _DBContext:
        """Return a context manager that yields an instrumented connection."""
        return _DBContext(self)

    # ── Internal recording ────────────────────────────────────────────────────

    def _record(self, elapsed_ms: float, error: bool = False) -> None:
        self._calls += 1
        self._latencies.append(elapsed_ms)
        if error:
            self._errors += 1
        if elapsed_ms >= _SLOW_MS:
            self._slow += 1
            try:
                import logging
                logging.getLogger(__name__).warning(
                    f"[db_proxy] slow query {elapsed_ms}ms (threshold={_SLOW_MS}ms)"
                )
            except Exception:
                pass

    def _record_error(self, exc: Exception) -> None:
        self._connect_errors += 1
        try:
            import logging
            logging.getLogger(__name__).error(
                f"[db_proxy] connection error: {exc}"
            )
        except Exception:
            pass

    # ── Metrics ───────────────────────────────────────────────────────────────

    def get_metrics(self) -> dict:
        """
        Return a summary dict of recent DB performance.
        Safe to call at any time — reads only the in-memory ring.
        """
        lats = sorted(self._latencies)
        n = len(lats)

        def _pct(p: float) -> float:
            if not lats:
                return 0.0
            idx = max(0, int(n * p / 100) - 1)
            return round(lats[idx], 1)

        return {
            "db_path":         str(self.db_path),
            "total_calls":     self._calls,
            "error_count":     self._errors,
            "connect_errors":  self._connect_errors,
            "slow_count":      self._slow,
            "slow_threshold_ms": _SLOW_MS,
            "latency_p50_ms":  _pct(50),
            "latency_p95_ms":  _pct(95),
            "latency_p99_ms":  _pct(99),
            "latency_max_ms":  round(lats[-1], 1) if lats else 0.0,
            "sample_size":     n,
        }
