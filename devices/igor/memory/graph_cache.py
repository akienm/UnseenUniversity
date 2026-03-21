"""
graph_cache.py — Redis hot-cache layer for frequently-traversed large graphs (D126).

GraphCache sits between WordGraph and the Postgres home DB. It is the
abstraction that makes large graphs (wg_cooccur: 29M rows) fast on every box
without Redis holding the full dataset.

Architecture:
  - Redis per box: top N words × M co-occur entries ≈ 250MB hot-cache
  - Postgres home DB: authoritative wg_cooccur master
  - Local DB: per-box access tracking (wg_access_log) + pending_replies queue

Gate: IGOR_REDIS_URL — if not set, all Redis paths are no-ops (Postgres direct).

Read path:  get_neighbors(words) → Redis ZUNIONSTORE → Postgres fallback
Write path: write_cooccur(pairs) → Redis + Postgres home; if home unreachable
            → enqueue to PendingReplyStore; retry on next drain

Cache maintenance:
  global_cache_refresh()  — prune to top-N by access count; daily cron
  global_cache_flush()    — full wipe + rebuild; when top-N composition changes
  prewarm(top_n)          — pre-fill Redis on boot from local access log
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from threading import Lock
from typing import Callable, Optional

from ..igor_base import IgorBase

log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────

_REDIS_URL = os.getenv("IGOR_REDIS_URL", "")  # e.g. redis://localhost:6379/0
_CACHE_MAX_WORDS = int(os.getenv("IGOR_WG_CACHE_WORDS", "10000"))  # top-N words cached
_CACHE_MAX_COOCCUR = int(
    os.getenv("IGOR_WG_CACHE_COOCCUR", "50")
)  # top-M neighbors/word
_PREWARM_ENABLED = os.getenv("IGOR_WG_PREWARM", "true").lower() == "true"

# Redis key namespace
_KEY_NS = "wg:cooccur:"  # wg:cooccur:{word_a} → ZSET {word_b: score}
_KEY_ACCESS = "wg:access:"  # wg:access:{word} → string (access count)
_KEY_REFRESH_TS = "wg:meta:last_refresh"

# Local DB schema for per-box access tracking
_ACCESS_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS wg_access_log (
    word         TEXT PRIMARY KEY,
    access_count INTEGER DEFAULT 1,
    last_access  TEXT
)
"""


class GraphCache(IgorBase):
    """
    Redis hot-cache for wg_cooccur with Postgres backing and pending-reply
    resilience (D126 Steps 2-5).

    Usage:
        gc = GraphCache(home_proxy, local_proxy)
        neighbors = gc.get_neighbors(["word1", "word2"], limit=50)
        gc.write_cooccur([("word_a", "word_b", 1.0), ...])
    """

    def __init__(
        self,
        home_proxy,  # make_home_proxy() — Postgres home (wg_cooccur master)
        local_proxy,  # make_local_proxy() — box-scoped (access log + pending_replies)
        pending_store=None,  # PendingReplyStore injected from outside
        max_words: int = _CACHE_MAX_WORDS,
        max_cooccur: int = _CACHE_MAX_COOCCUR,
    ) -> None:
        super().__init__()
        self._home = home_proxy
        self._local = local_proxy
        self._pending = pending_store
        self.max_words = max_words
        self.max_cooccur = max_cooccur
        self._redis = None  # lazy-initialized on first use
        self._redis_ok = False  # True once a successful connection is confirmed
        self._lock = Lock()
        self._access_writes: dict[str, int] = {}  # in-memory batch before DB flush
        self._ACCESS_FLUSH_EVERY = 100

        # Ensure local schema
        try:
            with self._local() as conn:
                conn.execute(_ACCESS_LOG_SCHEMA)
        except Exception as e:
            log.warning(f"[graph_cache] local schema init failed: {e}")

    # ── Redis connection ───────────────────────────────────────────────────────

    def _get_redis(self):
        """Return Redis client or None if unavailable."""
        if not _REDIS_URL:
            return None
        if self._redis is not None and self._redis_ok:
            return self._redis
        try:
            import redis

            r = redis.from_url(
                _REDIS_URL, socket_timeout=0.5, socket_connect_timeout=0.5
            )
            r.ping()
            self._redis = r
            self._redis_ok = True
            return r
        except Exception as e:
            log.debug(f"[graph_cache] Redis unavailable: {e}")
            self._redis_ok = False
            return None

    # ── Read path ─────────────────────────────────────────────────────────────

    def get_neighbors(
        self, words: list[str], limit: int = 50
    ) -> list[tuple[str, float]]:
        """
        Return top-`limit` co-occurrence neighbors for the given word(s).
        Redis → Postgres fallback.

        Returns [(word_b, score), ...] ordered by score desc.
        """
        if not words:
            return []

        r = self._get_redis()
        if r is not None:
            result = self._redis_get_neighbors(r, words, limit)
            if result is not None:
                self._record_access_batch(words)
                return result

        # Postgres / SQLite fallback
        return self._pg_get_neighbors(words, limit)

    def _redis_get_neighbors(
        self, r, words: list[str], limit: int
    ) -> list[tuple[str, float]] | None:
        """Try to serve from Redis. Returns None on any error (triggers fallback)."""
        try:
            if len(words) == 1:
                raw = r.zrevrange(_KEY_NS + words[0], 0, limit - 1, withscores=True)
                if not raw:
                    return None  # cache miss — fall through to Postgres
                return [
                    (w.decode() if isinstance(w, bytes) else w, float(s))
                    for w, s in raw
                ]
            else:
                # Multi-word: union scores across all word_a keys
                dest = f"tmp:union:{int(time.monotonic()*1e6)}"
                keys = [_KEY_NS + w for w in words]
                try:
                    r.zunionstore(dest, keys, aggregate="SUM")
                    raw = r.zrevrange(dest, 0, limit - 1, withscores=True)
                finally:
                    r.delete(dest)
                if not raw:
                    return None
                return [
                    (w.decode() if isinstance(w, bytes) else w, float(s))
                    for w, s in raw
                ]
        except Exception as e:
            log.debug(f"[graph_cache] Redis read error: {e}")
            return None

    # G-WG4: cap word list sent to Postgres to avoid slow plans on large IN (...)
    # Postgres with 29M rows in wg_cooccur: each extra word adds ~15ms to the query.
    # Top-20 words from the context carry most of the signal anyway.
    _MAX_QUERY_WORDS = 20

    def _pg_get_neighbors(
        self, words: list[str], limit: int
    ) -> list[tuple[str, float]]:
        """Query wg_cooccur from Postgres/SQLite home DB.

        G-WG4: limits the IN (...) clause to _MAX_QUERY_WORDS to keep Postgres
        query time bounded even for long context windows.
        """
        try:
            # Truncate to avoid O(n) query blowup on long word lists (G-WG4)
            if len(words) > self._MAX_QUERY_WORDS:
                words = words[: self._MAX_QUERY_WORDS]
            w_ph = ",".join("?" * len(words))
            with self._home() as conn:
                rows = conn.execute(
                    f"SELECT word_b, SUM(score) AS total FROM wg_cooccur "
                    f"WHERE word_a IN ({w_ph}) GROUP BY word_b "
                    f"ORDER BY total DESC LIMIT ?",
                    words + [limit],
                ).fetchall()
            return [(r[0], float(r[1])) for r in rows]
        except Exception as e:
            log.warning(f"[graph_cache] Postgres read error: {e}")
            return []

    # ── Write path ────────────────────────────────────────────────────────────

    def write_cooccur(self, pairs: list[tuple[str, str, float]]) -> None:
        """
        Dual-write co-occurrence pairs to Redis + Postgres home.
        On Postgres failure: enqueue to PendingReplyStore for later retry.

        pairs: [(word_a, word_b, score_delta), ...]
        """
        if not pairs:
            return

        r = self._get_redis()
        if r is not None:
            self._redis_write_cooccur(r, pairs)

        self._pg_write_cooccur(pairs)

    def _redis_write_cooccur(self, r, pairs: list[tuple[str, str, float]]) -> None:
        """Increment Redis ZSET scores for each (word_a, word_b) pair."""
        try:
            pipe = r.pipeline(transaction=False)
            for word_a, word_b, score in pairs:
                key = _KEY_NS + word_a
                pipe.zincrby(key, score, word_b)
                # Trim to max_cooccur immediately after write
                pipe.zremrangebyrank(key, 0, -(self.max_cooccur + 1))
            pipe.execute()
        except Exception as e:
            log.debug(f"[graph_cache] Redis write error: {e}")

    def _pg_write_cooccur(self, pairs: list[tuple[str, str, float]]) -> None:
        """Write pairs to home DB. On failure: enqueue pending reply."""
        try:
            with self._home() as conn:
                conn.executemany(
                    "INSERT INTO wg_cooccur (word_a, word_b, score) VALUES (?, ?, ?) "
                    "ON CONFLICT(word_a, word_b) DO UPDATE SET score = wg_cooccur.score + excluded.score",
                    pairs,
                )
        except Exception as e:
            log.warning(
                f"[graph_cache] home write failed ({e}) — queuing pending reply"
            )
            if self._pending is not None:
                self._pending.enqueue(
                    table="wg_cooccur",
                    op="upsert",
                    payload={"pairs": [(w_a, w_b, s) for w_a, w_b, s in pairs]},
                )

    # ── Access tracking ───────────────────────────────────────────────────────

    def _record_access_batch(self, words: list[str]) -> None:
        """Increment in-memory access counter; flush to local DB every N calls."""
        for w in words:
            self._access_writes[w] = self._access_writes.get(w, 0) + 1
        if len(self._access_writes) >= self._ACCESS_FLUSH_EVERY:
            self._flush_access_log()

    def _flush_access_log(self) -> None:
        """Persist in-memory access counts to wg_access_log in local DB."""
        if not self._access_writes:
            return
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        rows = [(w, cnt, now) for w, cnt in self._access_writes.items()]
        try:
            with self._local() as conn:
                conn.executemany(
                    "INSERT INTO wg_access_log (word, access_count, last_access) VALUES (?, ?, ?) "
                    "ON CONFLICT(word) DO UPDATE SET "
                    "access_count = access_count + excluded.access_count, "
                    "last_access = excluded.last_access",
                    rows,
                )
            self._access_writes.clear()
        except Exception as e:
            log.warning(f"[graph_cache] access log flush error: {e}")

    def get_my_top_n(self, n: int = None) -> list[str]:
        """Return the top-N most accessed words on this box."""
        n = n or self.max_words
        try:
            with self._local() as conn:
                rows = conn.execute(
                    "SELECT word FROM wg_access_log ORDER BY access_count DESC LIMIT ?",
                    (n,),
                ).fetchall()
            return [r[0] for r in rows]
        except Exception:
            return []

    # ── Cache prewarm ─────────────────────────────────────────────────────────

    def prewarm(self, top_n: int = None) -> int:
        """
        Pre-load top-N words into Redis from home DB on boot.
        Uses wg_access_log ranking if available; falls back to wg_cooccur frequency.
        Returns number of words loaded.

        Gate: IGOR_WG_PREWARM=false to disable.
        """
        if not _PREWARM_ENABLED:
            return 0
        r = self._get_redis()
        if r is None:
            return 0

        top_n = top_n or self.max_words
        log.info(f"[graph_cache] prewarm: loading top {top_n} words into Redis…")

        # Prefer box-local access ranking; fall back to global frequency
        top_words = self.get_my_top_n(top_n)
        if not top_words:
            try:
                with self._home() as conn:
                    rows = conn.execute(
                        "SELECT word_a, COUNT(*) as c FROM wg_cooccur "
                        "GROUP BY word_a ORDER BY c DESC LIMIT ?",
                        (top_n,),
                    ).fetchall()
                top_words = [r[0] for r in rows]
            except Exception as e:
                log.warning(f"[graph_cache] prewarm fallback query failed: {e}")
                return 0

        loaded = 0
        pipe = r.pipeline(transaction=False)
        for word in top_words:
            try:
                with self._home() as conn:
                    rows = conn.execute(
                        "SELECT word_b, score FROM wg_cooccur "
                        "WHERE word_a = ? ORDER BY score DESC LIMIT ?",
                        (word, self.max_cooccur),
                    ).fetchall()
                if rows:
                    key = _KEY_NS + word
                    mapping = {
                        (r[0].encode() if isinstance(r[0], str) else r[0]): float(r[1])
                        for r in rows
                    }
                    pipe.zadd(key, mapping)
                    loaded += 1
                    if loaded % 500 == 0:
                        pipe.execute()
                        pipe = r.pipeline(transaction=False)
            except Exception:
                continue
        try:
            pipe.execute()
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/graph_cache.py: %s", _bare_e
            )

        # Record prewarm timestamp
        try:
            r.set(_KEY_REFRESH_TS, time.strftime("%Y-%m-%dT%H:%M:%S"))
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/graph_cache.py: %s", _bare_e
            )

        log.info(f"[graph_cache] prewarm complete: {loaded} words loaded")
        return loaded

    # ── Cache maintenance ─────────────────────────────────────────────────────

    def global_cache_refresh(self, top_n: int = None) -> dict:
        """
        Prune Redis to top-N words by this box's access count.
        Remove entries for words no longer in top-N.
        Called by daily cron.

        Returns {"evicted": N, "retained": M}.
        """
        r = self._get_redis()
        if r is None:
            return {"evicted": 0, "retained": 0, "redis": "unavailable"}

        top_n = top_n or self.max_words
        self._flush_access_log()
        top_words = set(self.get_my_top_n(top_n))

        evicted = 0
        retained = 0
        try:
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match=f"{_KEY_NS}*", count=200)
                for key in keys:
                    word = (
                        key.decode().replace(_KEY_NS, "")
                        if isinstance(key, bytes)
                        else key.replace(_KEY_NS, "")
                    )
                    if word not in top_words:
                        r.delete(key)
                        evicted += 1
                    else:
                        # Trim to max_cooccur while we're here
                        r.zremrangebyrank(key, 0, -(self.max_cooccur + 1))
                        retained += 1
                if cursor == 0:
                    break
        except Exception as e:
            log.warning(f"[graph_cache] cache_refresh error: {e}")

        try:
            r.set(_KEY_REFRESH_TS, time.strftime("%Y-%m-%dT%H:%M:%S"))
        except Exception as _bare_e:
            logging.getLogger(__name__).warning(
                "bare except in wild_igor/igor/memory/graph_cache.py: %s", _bare_e
            )

        log.info(f"[graph_cache] cache_refresh: evicted={evicted} retained={retained}")
        return {"evicted": evicted, "retained": retained}

    def global_cache_flush(self) -> dict:
        """
        Full Redis wipe of wg:cooccur:* keys + rebuild from home DB.
        Called when top-N composition changes significantly.

        Returns {"flushed": N, "reloaded": M}.
        """
        r = self._get_redis()
        if r is None:
            return {"flushed": 0, "reloaded": 0, "redis": "unavailable"}

        flushed = 0
        try:
            cursor = 0
            while True:
                cursor, keys = r.scan(cursor, match=f"{_KEY_NS}*", count=500)
                if keys:
                    r.delete(*keys)
                    flushed += len(keys)
                if cursor == 0:
                    break
        except Exception as e:
            log.warning(f"[graph_cache] cache_flush wipe error: {e}")

        reloaded = self.prewarm()
        log.info(f"[graph_cache] cache_flush: flushed={flushed} reloaded={reloaded}")
        return {"flushed": flushed, "reloaded": reloaded}
