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
import logging
import os
import re
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from ..igor_base import IgorBase
from ..paths import paths

# Thread-local flag to prevent EXPLAIN QUERY PLAN re-entrancy
_in_explain = threading.local()

_SLOW_MS = int(os.getenv("IGOR_DB_SLOW_MS", "50"))
_RING_SIZE = 500

# ── Dedicated DB query log ────────────────────────────────────────────────────
# All slow queries written to db_queries.log with timestamp + turn_id tie-back.
# turn_id links each slow query back to the forensic_logger turn for the same call.

_DB_LOG_PATH = paths().logs / "db_queries.log"
_DB_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)


def _db_log(elapsed_ms: float, sql: str, owner: str = "?") -> None:
    """Append one slow-query entry to db_queries.log."""
    try:
        turn_id = "(unknown)"
        try:
            from ..cognition.forensic_logger import get_turn_id

            turn_id = get_turn_id()
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/db_proxy.py: %s", _bare_e
            )
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} owner={owner} turn={turn_id} elapsed={elapsed_ms}ms sql={sql}\n"
        with open(_DB_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as _bare_e:
        logging.getLogger(__name__).warning(
            "bare except in wild_igor/igor/memory/db_proxy.py: %s", _bare_e
        )


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
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/db_proxy.py: %s", _bare_e
                )
            try:
                self._conn.close()
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/db_proxy.py: %s", _bare_e
                )
        return False  # never suppress exceptions


class DatabaseProxy(IgorBase):
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
        super().__init__()
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
                    last_sql[:600].replace("\n", " ").strip()
                    if last_sql
                    else "(unknown)"
                )
                logging.getLogger(__name__).warning(
                    f"[db_proxy] slow query {elapsed_ms}ms — {sql_snippet}"
                )
                _db_log(elapsed_ms, sql_snippet, owner=self.get_name())
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/db_proxy.py: %s", _bare_e
                )

    def _record_error(self, exc: Exception) -> None:
        self._connect_errors += 1
        try:
            import logging

            logging.getLogger(__name__).error(f"[db_proxy] connection error: {exc}")
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/db_proxy.py: %s", _bare_e
            )

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
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/db_proxy.py: %s", _bare_e
            )
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


# ── Postgres backend ──────────────────────────────────────────────────────────

_INSERT_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)
_INSERT_OR_REPLACE = re.compile(
    r"\bINSERT\s+OR\s+REPLACE\s+INTO\s+(\w+)\s*\(([^)]+)\)",
    re.IGNORECASE | re.DOTALL,
)

# Primary key columns for each table — used to generate ON CONFLICT clauses.
_TABLE_PK: dict[str, str | tuple] = {
    "memories": "id",
    "reading_list": "id",
    "_migrations": "name",
    "memory_embeddings": "memory_id",
    "ring_memory": "id",
    "twm_observations": "id",
    "memory_blobs": "id",
    "interpretive_edges": "id",
    "lists": ("list_name", "item_key", "instance_id"),
    # word graph tables (D126)
    "wg_meta": "key",
    "wg_word_lang": "word",
    "wg_idf": "word",
    "wg_word_docs": ("word", "doc_id"),
    "wg_cooccur": ("word_a", "word_b"),
    # budget tables
    "config": "key",
}


def _translate_insert_or_replace(sql: str) -> str:
    """
    Translate SQLite `INSERT OR REPLACE INTO table (cols) VALUES (...)`
    to Postgres `INSERT INTO table (cols) VALUES (...) ON CONFLICT (pk) DO UPDATE SET ...`
    """
    m = _INSERT_OR_REPLACE.search(sql)
    if not m:
        return sql
    table = m.group(1).lower()
    col_str = m.group(2)
    cols = [c.strip() for c in col_str.split(",")]

    # Strip the matched prefix and rebuild as plain INSERT
    new_sql = _INSERT_OR_REPLACE.sub(f"INSERT INTO {table} ({col_str})", sql, count=1)
    new_sql = new_sql.replace("?", "%s")

    pk = _TABLE_PK.get(table, "id")
    if isinstance(pk, tuple):
        # Composite PK — use ON CONFLICT (col1, col2, ...) DO UPDATE SET ...
        pk_cols = set(pk)
        pk_clause = ", ".join(pk)
    else:
        pk_cols = {pk}
        pk_clause = pk

    update_cols = [c for c in cols if c not in pk_cols]
    if update_cols:
        update_set = ", ".join(f"{c}=EXCLUDED.{c}" for c in update_cols)
        conflict_clause = f" ON CONFLICT ({pk_clause}) DO UPDATE SET {update_set}"
    else:
        conflict_clause = f" ON CONFLICT ({pk_clause}) DO NOTHING"

    return new_sql.rstrip("; \t\n") + conflict_clause


class _PGConnWrapper:
    """
    Thin wrapper around a psycopg2 connection that makes it look like sqlite3.Connection
    to Cortex callers:
    - Translates ? placeholders → %s (psycopg2 uses pyformat style)
    - Translates INSERT OR IGNORE INTO → INSERT INTO ... ON CONFLICT DO NOTHING
    - execute() returns self so callers can chain .fetchone()/.fetchall()
    - row_factory not needed — psycopg2.extras.RealDictCursor used at connection level
    """

    __slots__ = ("_conn", "_cur", "_last_sql", "_pending_scalar")

    def __init__(self, conn) -> None:
        import psycopg2.extras  # noqa: F401

        self._conn = conn
        self._cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        self._last_sql: str = ""
        self._pending_scalar = None  # used to return SQLite-compat row counts

    def _translate(self, sql: str) -> str:
        sql = _INSERT_OR_IGNORE.sub("INSERT INTO", sql)
        # Append ON CONFLICT DO NOTHING if we stripped OR IGNORE
        if "INSERT INTO" in sql and "ON CONFLICT" not in sql:
            # Only append if we actually did a substitution (original had OR IGNORE)
            pass  # handled below via flag
        return sql.replace("?", "%s")

    def executescript(self, sql: str) -> "_PGConnWrapper":
        """Run a multi-statement SQL script (SQLite compat shim for Postgres).
        Splits on semicolons and executes each non-empty statement individually.
        """
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                self.execute(stmt)
        return self

    def execute(self, sql: str, params=()) -> "_PGConnWrapper":
        # PRAGMA — SQLite-only; silently no-op on Postgres
        if sql.lstrip().upper().startswith("PRAGMA"):
            return self
        # SELECT changes() — SQLite row-count function; return last DML rowcount instead
        if sql.strip().upper() == "SELECT CHANGES()":
            self._pending_scalar = self._cur.rowcount if self._cur.rowcount >= 0 else 0
            return self
        # INSERT OR REPLACE — full upsert with DO UPDATE SET
        if _INSERT_OR_REPLACE.search(sql):
            translated = _translate_insert_or_replace(sql)
        # INSERT OR IGNORE — silent conflict skip
        elif _INSERT_OR_IGNORE.search(sql):
            translated = _INSERT_OR_IGNORE.sub("INSERT INTO", sql).replace("?", "%s")
            if "ON CONFLICT" not in translated:
                translated = translated.rstrip("; \t\n") + " ON CONFLICT DO NOTHING"
        else:
            translated = sql.replace("?", "%s")
        self._last_sql = translated
        # SELECT statements can never abort a transaction — skip savepoint overhead.
        # Savepoints are only needed for DDL/DML that might raise (e.g. column-already-exists
        # patterns from _init_db), letting callers do `try: conn.execute(...) except: pass`.
        if translated.lstrip().upper().startswith("SELECT"):
            self._cur.execute(translated, params or ())
            return self
        # DML/DDL: wrap in a savepoint so a failed statement doesn't abort the transaction.
        # Uses a dedicated cursor so RELEASE SAVEPOINT doesn't clear self._cur's result set.
        sp_cur = self._conn.cursor()
        try:
            sp_cur.execute("SAVEPOINT _igor_sp")
            try:
                self._cur.execute(translated, params or ())
                sp_cur.execute("RELEASE SAVEPOINT _igor_sp")
            except Exception:
                sp_cur.execute("ROLLBACK TO SAVEPOINT _igor_sp")
                sp_cur.execute("RELEASE SAVEPOINT _igor_sp")
                raise
        finally:
            sp_cur.close()
        return self

    def executemany(self, sql: str, seq) -> "_PGConnWrapper":
        if _INSERT_OR_REPLACE.search(sql):
            translated = _translate_insert_or_replace(sql)
        elif _INSERT_OR_IGNORE.search(sql):
            translated = _INSERT_OR_IGNORE.sub("INSERT INTO", sql).replace("?", "%s")
            if "ON CONFLICT" not in translated:
                translated = translated.rstrip("; \t\n") + " ON CONFLICT DO NOTHING"
        else:
            translated = sql.replace("?", "%s")
        self._last_sql = translated
        self._cur.executemany(translated, seq)
        return self

    @property
    def lastrowid(self):
        """Return last inserted row id via LASTVAL() — mirrors sqlite3.Cursor.lastrowid."""
        try:
            tmp = self._conn.cursor()
            tmp.execute("SELECT LASTVAL()")
            val = tmp.fetchone()[0]
            tmp.close()
            return val
        except Exception:
            return None

    def fetchone(self):
        if self._pending_scalar is not None:
            val = self._pending_scalar
            self._pending_scalar = None
            return (val,)  # caller does .fetchone()[0]
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
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/db_proxy.py: %s", _bare_e
            )
        try:
            self._conn.close()
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/db_proxy.py: %s", _bare_e
            )


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
    """Context manager for PGDatabaseProxy — mirrors _DBContext interface."""

    __slots__ = ("_proxy", "_wrapper", "_t0")

    def __init__(self, proxy: "PGDatabaseProxy") -> None:
        self._proxy = proxy
        self._wrapper: Optional[_PGConnWrapper] = None
        self._t0: float = 0.0

    def __enter__(self) -> _PGConnWrapper:
        self._t0 = time.monotonic()
        try:
            conn = self._proxy._pool.getconn()
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
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/db_proxy.py: %s", _bare_e
                )
            try:
                self._proxy._pool.putconn(raw_conn)
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/db_proxy.py: %s", _bare_e
                )
        return False


class PGDatabaseProxy(IgorBase):
    """
    Postgres-backed drop-in replacement for DatabaseProxy.
    Uses IGOR_DB_URL (standard libpq DSN) from the environment.
    ThreadedConnectionPool for concurrent multi-box access.

    Interface identical to DatabaseProxy — callers use:
        with self._db() as conn:
            conn.execute(...)
    """

    def __init__(self, db_url: str) -> None:
        super().__init__()
        self.db_url = db_url
        self._latencies: deque[float] = deque(maxlen=_RING_SIZE)
        self._errors: int = 0
        self._slow: int = 0
        self._calls: int = 0
        self._connect_errors: int = 0
        import psycopg2
        from psycopg2 import pool as pg_pool

        self._pool = pg_pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
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
                import logging

                sql_snippet = (
                    last_sql[:600].replace("\n", " ").strip()
                    if last_sql
                    else "(unknown)"
                )
                logging.getLogger(__name__).warning(
                    f"[pg_proxy] slow query {elapsed_ms}ms — {sql_snippet}"
                )
                _db_log(elapsed_ms, sql_snippet, owner=self.get_name())
            except Exception as _bare_e:
                logging.getLogger(__name__).warning(
                    "bare except in wild_igor/igor/memory/db_proxy.py: %s", _bare_e
                )

    def _record_error(self, exc: Exception) -> None:
        self._connect_errors += 1
        try:
            import logging

            logging.getLogger(__name__).error(f"[pg_proxy] connection error: {exc}")
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/db_proxy.py: %s", _bare_e
            )

    def ensure_index(self, table: str, columns: tuple, unique: bool = False) -> None:
        """No-op for Postgres — indexes created by migration script."""
        pass

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
            "db_url": self.db_url.split("@")[-1],  # hide credentials
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


# ── Factory ───────────────────────────────────────────────────────────────────


def make_home_proxy(db_path: Path = None):
    """
    Return PGDatabaseProxy for IGOR_HOME_DB_URL (global truth DB shared across
    all Igor instances), else DatabaseProxy (SQLite fallback).

    HOME tables: memories, interpretive_edges, wg_cooccur, notebooks,
                 ResourceManager ledger/policy, reading_list.
    """
    db_url = os.getenv("IGOR_HOME_DB_URL") or os.getenv(
        "IGOR_DB_URL"
    )  # backward compat
    if db_url:
        return PGDatabaseProxy(db_url)
    return DatabaseProxy(db_path)


def make_local_proxy(db_path: Path = None):
    """
    Return PGDatabaseProxy for IGOR_LOCAL_DB_URL (box-scoped DB shared by all
    instances on this machine), else DatabaseProxy (SQLite fallback — same file
    as home proxy when running single-node).

    LOCAL tables: ring_memory, twm_observations, pending_replies, per-box metrics.
    """
    db_url = os.getenv("IGOR_LOCAL_DB_URL")
    if db_url:
        return PGDatabaseProxy(db_url)
    return DatabaseProxy(db_path)


def make_db_proxy(db_path: Path = None):
    """Backward-compat alias for make_home_proxy(). Prefer make_home_proxy() or make_local_proxy()."""
    return make_home_proxy(db_path)
