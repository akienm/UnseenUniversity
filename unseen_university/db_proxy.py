"""
db.py — Full Postgres database proxy for unseen_university.

Canonical home for DatabaseProxy infrastructure. No TheIgors imports, no SQLite.
wild_igor/igor/memory/db_proxy.py re-exports from here.

Callers:
    proxy = make_home_proxy()   # Igor's clan DB (IGOR_HOME_DB_URL)
    proxy = make_local_proxy()  # Igor's instance DB
    proxy = make_infra_proxy()  # infra schema only (UC services)
    proxy = make_dc_proxy()     # unseen_university's own DB (AGENT_DATACENTER_DB_URL)

    with proxy() as conn:
        conn.execute("SELECT ...")
# tags: Infrastructure, Memory
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_SLOW_MS = int(os.getenv("IGOR_DB_SLOW_MS", "50"))
_RING_SIZE = 500

# Lazy-init guard for the slow_queries.log file handler.
_slow_handler_lock = threading.Lock()
_slow_handler_installed = False


def _ensure_slow_query_handler() -> None:
    """Attach a RotatingFileHandler for slow_queries.log on first slow query."""
    global _slow_handler_installed
    if _slow_handler_installed:
        return
    with _slow_handler_lock:
        if _slow_handler_installed:
            return
        try:
            slow_path = _get_db_log_path().parent / "slow_queries.log"
            slow_path.parent.mkdir(parents=True, exist_ok=True)
            h = logging.handlers.RotatingFileHandler(
                slow_path, maxBytes=10 * 1024 * 1024, backupCount=1, encoding="utf-8"
            )
            h.setLevel(logging.WARNING)
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            log.addHandler(h)
            _slow_handler_installed = True
        except Exception as e:
            log.warning("could not set up slow_queries.log handler: %s", e)


# D200: memory column list owned here — cortex imports this constant so SQL
# construction stays in the data layer. Excludes the embedding blob.
MEM_COLS = (
    "id, narrative, memory_type, parent_id, children_ids, link_ids, "
    "valence, activation_count, friction_history, timestamp, metadata, "
    "arousal, dominance, portable, links_weighted, last_accessed, "
    "source, confidence, context_of_encoding, scope, payload"
)

_DB_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB


def _get_db_log_path() -> Path:
    """Return path for db_queries.log; respects IGOR_LOG_DIR env override."""
    log_dir = os.getenv("IGOR_LOG_DIR")
    if log_dir:
        return Path(log_dir) / "db_queries.log"
    return Path.home() / ".TheIgors" / "logs" / "db_queries.log"


def _db_log(elapsed_ms: float, sql: str, owner: str = "?") -> None:
    """Append one slow-query entry to db_queries.log."""
    try:
        path = _get_db_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > _DB_LOG_MAX_BYTES:
            backup = path.with_suffix(".log.1")
            if backup.exists():
                backup.unlink()
            path.rename(backup)
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{ts} owner={owner} elapsed={elapsed_ms}ms sql={sql}\n")
    except Exception as e:
        log.warning("_db_log write failed: %s", e)


# ---------------------------------------------------------------------------
# Connection wrapper
# ---------------------------------------------------------------------------


class _PGConnWrapper:
    """
    Thin wrapper around a psycopg2 connection that makes it look like sqlite3.Connection
    to Cortex callers:
    - execute() returns self so callers can chain .fetchone()/.fetchall()
    - row_factory not needed — psycopg2.extras.RealDictCursor used at connection level
    - All callers use native Postgres syntax (no SQLite translation needed)
    """

    __slots__ = ("_conn", "_cur", "_last_sql")

    def __init__(self, conn) -> None:
        import psycopg2.extras

        self._conn = conn
        self._cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        self._last_sql: str = ""

    def execute(self, sql: str, params=()) -> "_PGConnWrapper":
        self._last_sql = sql
        # SELECT statements can never abort a transaction — skip savepoint overhead.
        # Savepoints only needed for DDL/DML that might raise (e.g. column-already-exists
        # patterns from _init_db), letting callers do `try: conn.execute(...) except: pass`.
        if sql.lstrip().upper().startswith("SELECT"):
            self._cur.execute(sql, params or ())
            return self
        sp_cur = self._conn.cursor()
        try:
            sp_cur.execute("SAVEPOINT _igor_sp")
            try:
                self._cur.execute(sql, params or ())
                sp_cur.execute("RELEASE SAVEPOINT _igor_sp")
            except Exception:
                sp_cur.execute("ROLLBACK TO SAVEPOINT _igor_sp")
                sp_cur.execute("RELEASE SAVEPOINT _igor_sp")
                raise
        finally:
            sp_cur.close()
        return self

    def executemany(self, sql: str, seq) -> "_PGConnWrapper":
        self._last_sql = sql
        self._cur.executemany(sql, seq)
        return self

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount if self._cur.rowcount >= 0 else 0

    def fetchone(self):
        row = self._cur.fetchone()
        if row is None:
            return None
        return _PGRowProxy(row)

    def fetchall(self):
        return [_PGRowProxy(r) for r in self._cur.fetchall()]

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        try:
            self._cur.close()
        except Exception as e:
            log.warning("close cursor error: %s", e)
        try:
            self._conn.close()
        except Exception as e:
            log.warning("close conn error: %s", e)


class _PGRowProxy:
    """
    Makes psycopg2 RealDictRow act like sqlite3.Row:
    supports both row["col"] and row[0] (integer index) access.
    """

    __slots__ = ("_d", "_keys")

    def __init__(self, row) -> None:
        self._d = dict(row)
        self._keys = list(self._d.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._d[self._keys[key]]
        return self._d[key]

    def __iter__(self):
        return iter(self._d.values())

    def keys(self):
        return self._keys

    def get(self, key, default=None):
        return self._d.get(key, default)


class _PGContext:
    """Context manager for PGDatabaseProxy — opens a pooled connection, commits/rolls back on exit."""

    __slots__ = ("_proxy", "_wrapper", "_t0")

    def __init__(self, proxy: "PGDatabaseProxy") -> None:
        self._proxy = proxy
        self._wrapper: Optional[_PGConnWrapper] = None
        self._t0: float = 0.0

    def __enter__(self) -> _PGConnWrapper:
        self._t0 = time.monotonic()
        try:
            conn = self._proxy._pool.getconn()
            cur = conn.cursor()
            cur.execute(f"SET search_path TO {self._proxy._search_path}")
            cur.close()
            conn.commit()
            self._wrapper = _PGConnWrapper(conn)
            return self._wrapper
        except Exception as exc:
            self._proxy._record_error(exc)
            raise

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        elapsed_ms = round((time.monotonic() - self._t0) * 1000)
        last_sql = self._wrapper._last_sql if self._wrapper else ""
        self._proxy._record(elapsed_ms, error=exc_type is not None, last_sql=last_sql)
        if self._wrapper is not None:
            raw_conn = self._wrapper._conn
            try:
                if exc_type is None:
                    raw_conn.commit()
                else:
                    raw_conn.rollback()
            except Exception as e:
                log.warning("transaction finalise error: %s", e)
            try:
                self._proxy._pool.putconn(raw_conn)
            except Exception as e:
                log.warning("putconn error: %s", e)
        return False


# ---------------------------------------------------------------------------
# Proxy class
# ---------------------------------------------------------------------------


class PGDatabaseProxy:
    """
    Postgres-backed database proxy with connection pooling and slow-query telemetry.

    Usage:
        proxy = make_home_proxy()
        with proxy() as conn:
            conn.execute("SELECT ...")

    search_path controls which Postgres schemas are visible:
        make_home_proxy()   → clan,infra,public
        make_local_proxy()  → instance,clan,infra,public
        make_infra_proxy()  → infra,public
        make_dc_proxy()     → public
    """

    DEFAULT_SEARCH_PATH = "instance,clan,infra,public"

    def __init__(self, db_url: str, search_path: str = None) -> None:
        self.db_url = db_url
        self._search_path = search_path or self.DEFAULT_SEARCH_PATH
        self._latencies: deque[float] = deque(maxlen=_RING_SIZE)
        self._errors: int = 0
        self._slow: int = 0
        self._calls: int = 0
        self._connect_errors: int = 0
        from psycopg2 import pool as pg_pool

        self._pool = pg_pool.ThreadedConnectionPool(
            minconn=3,
            maxconn=20,
            dsn=db_url,
        )

    def __call__(self) -> _PGContext:
        return _PGContext(self)

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
                sql_snippet = (
                    last_sql[:600].replace("\n", " ").strip()
                    if last_sql
                    else "(unknown)"
                )
                _ensure_slow_query_handler()
                log.warning("slow query %dms — %s", elapsed_ms, sql_snippet)
                _db_log(elapsed_ms, sql_snippet, owner=type(self).__name__)
            except Exception as e:
                log.warning("_record telemetry error: %s", e)

    def _record_error(self, exc: Exception) -> None:
        self._connect_errors += 1
        log.error("connection error: %s", exc)

    def ensure_index(self, table: str, columns: tuple, unique: bool = False) -> None:
        """No-op for Postgres — indexes created by migration script."""

    def get_index_report(self) -> dict:
        return {}

    def get_metrics(self) -> dict:
        lats = sorted(self._latencies)
        n = len(lats)

        def _pct(p: float) -> float:
            if not lats:
                return 0.0
            idx = max(0, int(n * p / 100) - 1)
            return round(lats[idx], 1)

        return {
            "db_url": self.db_url.split("@")[-1],
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

    def fetch_by_ids(self, ids: list, excl_types: tuple = ()) -> list:
        """Fetch memory rows by ID list. Returns raw rows; caller maps to Memory."""
        if not ids:
            return []
        ph = ",".join(["%s"] * len(ids))
        if excl_types:
            excl_ph = ",".join(["%s"] * len(excl_types))
            sql = (
                f"SELECT {MEM_COLS} FROM memories "
                f"WHERE id IN ({ph}) AND memory_type NOT IN ({excl_ph})"
            )
            params = list(ids) + list(excl_types)
        else:
            sql = f"SELECT {MEM_COLS} FROM memories WHERE id IN ({ph})"
            params = list(ids)
        with self() as conn:
            return conn.execute(sql, params).fetchall()

    def get_activation_rows(self, limit: int, since_hours: float = 48.0) -> list:
        """Return (node_id, last_seen) rows for hottest tails entries in the window."""
        from datetime import datetime, timedelta

        cutoff = (datetime.now() - timedelta(hours=since_hours)).isoformat()
        with self() as conn:
            return conn.execute(
                "SELECT node_id, MAX(recorded_at) as last_seen "
                "FROM tails WHERE recorded_at > %s "
                "GROUP BY node_id ORDER BY last_seen DESC LIMIT %s",
                (cutoff, limit),
            ).fetchall()


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def make_home_proxy(db_path=None) -> PGDatabaseProxy:
    """
    Return PGDatabaseProxy for IGOR_HOME_DB_URL (global truth DB shared across
    all Igor instances).

    HOME tables: clan.memories, clan.interpretive_edges, clan.wg_cooccur,
                 clan.reading_list, plus infra.* for cross-agent tables.
    search_path: clan,infra,public — no instance schema access.

    IGOR_HOME_SEARCH_PATH overrides (used by test fixtures for isolated schemas).
    """
    db_url = os.getenv("IGOR_HOME_DB_URL") or os.getenv("IGOR_DB_URL")
    if not db_url:
        raise RuntimeError(
            "IGOR_HOME_DB_URL not set — export IGOR_HOME_DB_URL=postgresql://..."
        )
    sp = os.getenv("IGOR_HOME_SEARCH_PATH") or "clan,infra,public"
    return PGDatabaseProxy(db_url, search_path=sp)


def make_local_proxy(db_path=None) -> PGDatabaseProxy:
    """
    Return PGDatabaseProxy for LOCAL tables (instance.ring_memory,
    instance.twm_observations, instance.pending_replies, per-box metrics).

    Checks IGOR_LOCAL_DB_URL first, falls back to IGOR_HOME_DB_URL.
    search_path: instance,clan,infra,public — full access for Igor.

    IGOR_LOCAL_SEARCH_PATH overrides (used by test fixtures).
    """
    db_url = (
        os.getenv("IGOR_LOCAL_DB_URL")
        or os.getenv("IGOR_HOME_DB_URL")
        or os.getenv("IGOR_DB_URL")
    )
    if not db_url:
        raise RuntimeError(
            "IGOR_HOME_DB_URL not set — export IGOR_HOME_DB_URL=postgresql://..."
        )
    sp = os.getenv("IGOR_LOCAL_SEARCH_PATH") or "instance,clan,infra,public"
    return PGDatabaseProxy(db_url, search_path=sp)


def make_infra_proxy() -> Optional[PGDatabaseProxy]:
    """
    Return PGDatabaseProxy for infrastructure tables only (infra schema).
    Used by rack server services that don't need clan or instance access.
    Returns None if IGOR_HOME_DB_URL is not set.
    """
    db_url = os.getenv("IGOR_HOME_DB_URL") or os.getenv("IGOR_DB_URL")
    if not db_url:
        return None
    return PGDatabaseProxy(db_url, search_path="infra,public")


def make_dc_proxy() -> PGDatabaseProxy:
    """
    Return a PGDatabaseProxy for agent-datacenter-0001.

    Reads AGENT_DATACENTER_DB_URL from the environment.
    AGENT_DATACENTER_POSTGRES_URL is accepted as a back-compat alias.
    """
    url = os.environ.get("AGENT_DATACENTER_DB_URL") or os.environ.get(
        "AGENT_DATACENTER_POSTGRES_URL"
    )
    if not url:
        raise RuntimeError(
            "AGENT_DATACENTER_DB_URL not set — "
            "export AGENT_DATACENTER_DB_URL=postgresql://datacenter:...@host/agent-datacenter-0001"
        )
    if not url.startswith("postgresql"):
        raise RuntimeError(
            f"AGENT_DATACENTER_DB_URL must be a postgresql:// URL, got: {url!r}"
        )
    return PGDatabaseProxy(url, search_path="public")


def make_db_proxy(db_path=None) -> PGDatabaseProxy:
    """Backward-compat alias for make_home_proxy(). Prefer make_home_proxy() or make_local_proxy()."""
    return make_home_proxy(db_path)


# Backward-compat alias — callers that import DatabaseProxy as a type annotation
# continue to work. SQLite DatabaseProxy was retired with T-remove-sqlite-fallback.
DatabaseProxy = PGDatabaseProxy
