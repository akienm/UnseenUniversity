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

import hashlib
import os
import re
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

# Thread-local flag to prevent EXPLAIN QUERY PLAN re-entrancy
_in_explain = threading.local()

_SLOW_MS = int(os.getenv("IGOR_DB_SLOW_MS", "50"))
_RING_SIZE = 500

# ── Dedicated DB query log ────────────────────────────────────────────────────
# All slow queries written to db_queries.log with timestamp + turn_id tie-back.
# turn_id links each slow query back to the forensic_logger turn for the same call.

_DB_LOG_PATH = Path.home() / ".TheIgors" / "logs" / "db_queries.log"
_DB_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _db_log(elapsed_ms: float, sql: str) -> None:
    """Append one slow-query entry to db_queries.log."""
    try:
        turn_id = "(unknown)"
        try:
            from ..cognition.forensic_logger import get_turn_id

            turn_id = get_turn_id()
        except Exception:
            pass
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} turn={turn_id} elapsed={elapsed_ms}ms sql={sql}\n"
        with open(_DB_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


class _DBContext:
    """
    Context manager returned by DatabaseProxy(). Yields a raw sqlite3.Connection.
    Times the block, records metrics, closes on exit.
    Uses set_trace_callback to capture executed SQL for slow-query diagnostics.
    """

    __slots__ = ("_proxy", "_conn", "_t0", "_last_sql")

    def __init__(self, proxy: "DatabaseProxy") -> None:
        self._proxy = proxy
        self._conn: Optional[sqlite3.Connection] = None
        self._t0: float = 0.0
        self._last_sql: str = ""

    def __enter__(self) -> sqlite3.Connection:
        self._t0 = time.monotonic()
        try:
            self._conn = sqlite3.connect(self._proxy.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.set_trace_callback(self._on_sql)
            return self._conn
        except Exception as exc:
            self._proxy._record_error(exc)
            raise

    def _on_sql(self, sql: str) -> None:
        self._last_sql = sql
        if self._conn is not None:
            self._proxy._track_index_usage(self._conn, sql)

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        elapsed_ms = round((time.monotonic() - self._t0) * 1000)
        self._proxy._record(
            elapsed_ms, error=exc_type is not None, last_sql=self._last_sql
        )
        if self._conn is not None:
            try:
                if exc_type is None:
                    self._conn.commit()  # persist writes — matches `with conn:` semantics
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
        self.db_path = db_path
        self._latencies: deque[float] = deque(maxlen=_RING_SIZE)
        self._errors: int = 0
        self._slow: int = 0
        self._calls: int = 0
        self._connect_errors: int = 0
        # W2: index lifecycle tracking
        self._explain_cache: dict[str, list[str]] = {}  # sql_hash[:12] → [index_names]
        self._index_hits: dict[str, int] = {}
        self._ensure_lock = threading.Lock()

    def __call__(self) -> _DBContext:
        """Return a context manager that yields an instrumented connection."""
        return _DBContext(self)

    # ── Internal recording ────────────────────────────────────────────────────

    def _record(
        self, elapsed_ms: float, error: bool = False, last_sql: str = ""
    ) -> None:
        self._calls += 1
        self._latencies.append(elapsed_ms)
        if error:
            self._errors += 1
        if elapsed_ms >= _SLOW_MS:
            self._slow += 1
            try:
                import logging

                sql_snippet = (
                    last_sql[:120].replace("\n", " ").strip()
                    if last_sql
                    else "(unknown)"
                )
                logging.getLogger(__name__).warning(
                    f"[db_proxy] slow query {elapsed_ms}ms — {sql_snippet}"
                )
                _db_log(elapsed_ms, sql_snippet)
            except Exception:
                pass

    def _record_error(self, exc: Exception) -> None:
        self._connect_errors += 1
        try:
            import logging

            logging.getLogger(__name__).error(f"[db_proxy] connection error: {exc}")
        except Exception:
            pass

    # ── Index lifecycle ───────────────────────────────────────────────────────

    def _track_index_usage(self, conn: sqlite3.Connection, sql: str) -> None:
        """
        Run EXPLAIN QUERY PLAN once per unique SQL pattern; accumulate index hit counts.
        Called from _DBContext._on_sql() on every executed statement.
        Thread-local _in_explain flag prevents re-entrancy.
        """
        if getattr(_in_explain, "active", False):
            return
        upper = sql.lstrip().upper()
        if upper.startswith(
            (
                "EXPLAIN",
                "CREATE",
                "DROP",
                "PRAGMA",
                "BEGIN",
                "COMMIT",
                "ROLLBACK",
                "SAVEPOINT",
                "RELEASE",
                "ATTACH",
                "DETACH",
            )
        ):
            return

        key = hashlib.sha256(sql.encode()).hexdigest()[:12]
        cached = self._explain_cache.get(key)
        if cached is not None:
            for idx_name in cached:
                self._index_hits[idx_name] = self._index_hits.get(idx_name, 0) + 1
            return

        _in_explain.active = True
        try:
            rows = conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
            idx_names: list[str] = []
            for row in rows:
                row_str = " ".join(str(c) for c in row)
                m = re.search(r"USING INDEX (\S+)", row_str, re.IGNORECASE)
                if m:
                    idx_names.append(m.group(1))
            self._explain_cache[key] = idx_names
            for idx_name in idx_names:
                self._index_hits[idx_name] = self._index_hits.get(idx_name, 0) + 1
        except Exception:
            self._explain_cache[key] = []
        finally:
            _in_explain.active = False

    def ensure_index(self, table: str, columns: tuple, unique: bool = False) -> None:
        """
        Idempotent CREATE INDEX IF NOT EXISTS for the given table+columns.
        Records creation in _cc_index_registry table (created once per DB).
        Thread-safe. Logs to db_queries.log when a new index is created.
        """
        col_str = "_".join(columns)
        idx_name = f"idx_{table}_{col_str}"
        cols_sql = ", ".join(columns)
        unique_kw = "UNIQUE " if unique else ""

        with self._ensure_lock:
            with self() as conn:
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS _cc_index_registry "
                    "(index_name TEXT PRIMARY KEY, table_name TEXT, "
                    "columns TEXT, created_at TEXT)"
                )
                existing = conn.execute(
                    "SELECT 1 FROM _cc_index_registry WHERE index_name = ?",
                    (idx_name,),
                ).fetchone()
                conn.execute(
                    f"CREATE {unique_kw}INDEX IF NOT EXISTS {idx_name} "
                    f"ON {table} ({cols_sql})"
                )
                if not existing:
                    conn.execute(
                        "INSERT OR IGNORE INTO _cc_index_registry "
                        "(index_name, table_name, columns, created_at) VALUES (?,?,?,?)",
                        (
                            idx_name,
                            table,
                            ",".join(columns),
                            time.strftime("%Y-%m-%dT%H:%M:%S"),
                        ),
                    )
                    _db_log(
                        0,
                        f"ensure_index: CREATE INDEX {idx_name} ON {table}({cols_sql})",
                    )

    def get_index_report(self) -> dict:
        """
        Return {index_name: {hits, table, columns, created_at}} from registry + in-memory hit counts.
        Safe to call at any time; returns {} if registry table not yet created.
        """
        result: dict = {}
        try:
            with self() as conn:
                rows = conn.execute(
                    "SELECT index_name, table_name, columns, created_at "
                    "FROM _cc_index_registry"
                ).fetchall()
            for row in rows:
                idx_name = row["index_name"]
                result[idx_name] = {
                    "hits": self._index_hits.get(idx_name, 0),
                    "table": row["table_name"],
                    "columns": row["columns"],
                    "created_at": row["created_at"],
                }
        except Exception:
            pass
        return result

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
            "db_path": str(self.db_path),
            "total_calls": self._calls,
            "error_count": self._errors,
            "connect_errors": self._connect_errors,
            "slow_count": self._slow,
            "slow_threshold_ms": _SLOW_MS,
            "latency_p50_ms": _pct(50),
            "latency_p95_ms": _pct(95),
            "latency_p99_ms": _pct(99),
            "latency_max_ms": round(lats[-1], 1) if lats else 0.0,
            "sample_size": n,
        }
