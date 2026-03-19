"""
redis_word_graph.py — Redis-backed word graph (D121).

Replaces SQLite wg_cooccur with Redis sorted sets:
  wg:cooccur:{word_a}  → ZSET  member=word_b  score=co-occurrence_count
  wg:wd:{word}         → ZSET  member=doc_id  score=weight  (parsing direction)
  wg:idf:{word}        → STRING float  (IDF weight)
  wg:lang:{word}       → STRING lang_tag
  wg:meta:{key}        → STRING value  (e.g. doc_count)

Benefits over SQLite (D121):
  - Network-accessible: all boxes share one graph — no divergence.
  - ZREVRANGE = single key lookup for predict_next, no GROUP BY over 29M rows.
  - Horizontal: Redis Sentinel for HA when ready.

Shadow mode (initial): writes go to BOTH Redis and SQLite.
score() delegates to SQLite WordGraph until full Redis migration.
Flip to Redis-only when redis_migrate_wg.py completes.

Gate: IGOR_REDIS_WORD_GRAPH_HOST (default unset → use SQLite WordGraph)
      IGOR_REDIS_WORD_GRAPH_PORT (default 6379)
      IGOR_REDIS_WG_SHADOW (default true — write to both; false = Redis-only)

Migration: claudecode/redis_migrate_wg.py
"""

from __future__ import annotations

import logging
import math
import os
import threading
from pathlib import Path
from typing import Optional

from ..igor_base import IgorBase
from .word_graph import WordGraph, tokenize_with_bigrams

_log = logging.getLogger(__name__)

_REDIS_HOST = os.getenv("IGOR_REDIS_WORD_GRAPH_HOST", "")
_REDIS_PORT = int(os.getenv("IGOR_REDIS_WORD_GRAPH_PORT", "6379"))
_SHADOW_MODE = os.getenv("IGOR_REDIS_WG_SHADOW", "true").lower() in ("1", "true", "yes")

# ── Key helpers ───────────────────────────────────────────────────────────────


def _cooccur_key(word: str) -> str:
    return f"wg:cooccur:{word}"


def _wd_key(word: str) -> str:
    return f"wg:wd:{word}"


def _idf_key(word: str) -> str:
    return f"wg:idf:{word}"


def _lang_key(word: str) -> str:
    return f"wg:lang:{word}"


_META_KEY = "wg:meta"

# ── In-process TTL prediction cache (mirrors SQLite WordGraph cache) ──────────

_PREDICT_CACHE_MAX = 512


class RedisWordGraph(IgorBase):
    """
    Redis-backed word graph implementing the same public API as WordGraph.

    Shadow mode: index() writes to BOTH Redis and the fallback SQLite WordGraph.
    score() always uses the fallback SQLite graph (preserves existing TF-IDF data).
    predict_next() queries Redis first; falls back to SQLite on error.

    Migration status: pending (redis_migrate_wg.py).
    Once migration completes and IGOR_REDIS_WG_SHADOW=false, SQLite is unused.
    """

    def __init__(
        self,
        name: str = "word_graph",
        db_path: Path | None = None,
        redis_host: str = _REDIS_HOST,
        redis_port: int = _REDIS_PORT,
    ) -> None:
        super().__init__()
        self.name = name
        self._lock = threading.RLock()
        self._shadow = _SHADOW_MODE

        # Redis connection (lazy — checked on first use)
        self._redis_host = redis_host or "localhost"
        self._redis_port = redis_port
        self._redis: Optional[object] = None  # redis.Redis instance

        # Fallback SQLite graph (always initialised)
        self._sqlite = WordGraph(name=name, db_path=db_path)

        # Prediction cache (LRU-style, same pattern as WordGraph)
        self._predict_cache: dict[tuple, list] = {}

    # ── Redis connection ──────────────────────────────────────────────────────

    def _get_redis(self):
        """Return live Redis client, or None if unavailable."""
        if self._redis is not None:
            return self._redis
        try:
            import redis as _redis_mod

            r = _redis_mod.Redis(
                host=self._redis_host,
                port=self._redis_port,
                socket_connect_timeout=2,
                socket_timeout=5,
                decode_responses=True,
            )
            r.ping()
            self._redis = r
            _log.info(
                "[redis_wg] connected to %s:%s", self._redis_host, self._redis_port
            )
        except Exception as exc:
            _log.warning(
                "[redis_wg] connection failed (%s) — falling back to SQLite", exc
            )
            self._redis = None
        return self._redis

    # ── Backward-compat properties ────────────────────────────────────────────

    @property
    def _word_to_ids(self):
        return self._sqlite._word_to_ids

    @property
    def _doc_count(self) -> int:
        r = self._get_redis()
        if r:
            try:
                v = r.hget(_META_KEY, "doc_count")
                return int(v) if v else 0
            except Exception:
                pass
        return self._sqlite._doc_count

    # ── Indexing ──────────────────────────────────────────────────────────────

    def index(
        self, doc_id: str, text: str, weight: float = 1.0, lang: str = "en"
    ) -> None:
        """Index document: writes to Redis (co-occurrence) and SQLite (shadow)."""
        with self._lock:
            # Always write to SQLite in shadow mode (preserves score() data)
            if self._shadow:
                self._sqlite.index(doc_id, text, weight=weight, lang=lang)

            r = self._get_redis()
            if r is None:
                return

            try:
                tokens = list(tokenize_with_bigrams(text, lang=lang))
                if not tokens:
                    return

                pipe = r.pipeline(transaction=False)

                # Co-occurrence: ZINCRBY for each pair in a sliding window
                window = min(len(tokens), 10)
                for i, word_a in enumerate(tokens):
                    for word_b in tokens[max(0, i - window) : i + window + 1]:
                        if word_b != word_a:
                            pipe.zincrby(_cooccur_key(word_a), 1.0, word_b)

                # Word-doc: track which docs contain each word (for score())
                for word in set(tokens):
                    pipe.zincrby(_wd_key(word), weight, doc_id)
                    pipe.setnx(_lang_key(word), lang)

                # doc_count
                pipe.hincrby(_META_KEY, "doc_count", 1)

                pipe.execute()
                # Invalidate predict cache on new training
                self._predict_cache.clear()

            except Exception as exc:
                _log.warning("[redis_wg] index() error: %s", exc)

    def build_idf(self) -> None:
        """Build IDF weights from current doc_count and word-doc data."""
        if self._shadow:
            self._sqlite.build_idf()
        r = self._get_redis()
        if r is None:
            return
        try:
            doc_count = self._doc_count
            if doc_count == 0:
                return
            # Scan all wg:wd:* keys and compute IDF
            pipe = r.pipeline(transaction=False)
            for key in r.scan_iter("wg:wd:*", count=500):
                word = key[len("wg:wd:") :]
                df = r.zcard(key)
                if df > 0:
                    idf = math.log(doc_count / df)
                    pipe.set(_idf_key(word), str(idf))
            pipe.execute()
        except Exception as exc:
            _log.warning("[redis_wg] build_idf() error: %s", exc)

    def flush_doc_count(self) -> None:
        if self._shadow:
            self._sqlite.flush_doc_count()

    # ── Parsing direction: score() — delegates to SQLite ─────────────────────

    def score(
        self, input_text: str, doc_ids: list[str], lang: str | None = None
    ) -> dict[str, float]:
        """
        Score doc_ids by TF-IDF overlap. Delegates to SQLite graph.

        Migration note: full Redis scoring (ZUNIONSTORE) deferred until
        wg:wd:* and wg:idf:* are fully migrated from SQLite.
        """
        return self._sqlite.score(input_text, doc_ids, lang=lang)

    # ── Generation direction: predict_next() — uses Redis ────────────────────

    def predict_next(
        self,
        context_text: str,
        n: int = 5,
        lang: str | None = None,
        milieu_state: dict | None = None,
    ) -> list[tuple[str, float]]:
        """
        Predict next words from co-occurrence. Uses Redis; falls back to SQLite.
        """
        words = list(tokenize_with_bigrams(context_text))
        if not words:
            return []

        fetch = n * 3 if milieu_state else n
        _cache_key = (tuple(sorted(words)), lang, fetch)
        if _cache_key in self._predict_cache:
            rows = self._predict_cache[_cache_key]
        else:
            rows = self._redis_predict(words, fetch, lang)
            if rows is None:
                # Redis unavailable — fall through to SQLite
                return self._sqlite.predict_next(
                    context_text, n=n, lang=lang, milieu_state=milieu_state
                )
            if len(self._predict_cache) >= _PREDICT_CACHE_MAX:
                # Evict half
                keys = list(self._predict_cache.keys())
                for k in keys[: _PREDICT_CACHE_MAX // 2]:
                    del self._predict_cache[k]
            self._predict_cache[_cache_key] = rows

        if not rows:
            return []

        # Milieu tilt (same logic as SQLite WordGraph)
        if milieu_state:
            arousal = float(milieu_state.get("arousal", 0.5))
            tilt = 1.0 + (arousal - 0.5)  # [0.5, 1.5]
            rows = [(w, s**tilt) for w, s in rows]
            rows.sort(key=lambda x: x[1], reverse=True)

        return rows[:n]

    def _redis_predict(
        self, words: list[str], fetch: int, lang: str | None
    ) -> list[tuple[str, float]] | None:
        """Query Redis for co-occurrences. Returns None if Redis unavailable."""
        r = self._get_redis()
        if r is None:
            return None
        try:
            # Aggregate co-occurrence across all input words
            aggregated: dict[str, float] = {}
            for word in words:
                results = r.zrevrange(_cooccur_key(word), 0, fetch - 1, withscores=True)
                for neighbor, score in results:
                    if neighbor not in words:  # exclude input words from predictions
                        aggregated[neighbor] = aggregated.get(neighbor, 0.0) + score

            if not aggregated:
                return []

            # Filter by lang if requested
            if lang:
                filtered = {}
                pipe = r.pipeline(transaction=False)
                lang_words = list(aggregated.keys())
                for w in lang_words:
                    pipe.get(_lang_key(w))
                lang_tags = pipe.execute()
                for w, tag in zip(lang_words, lang_tags):
                    if tag == lang:
                        filtered[w] = aggregated[w]
                aggregated = filtered

            if not aggregated:
                return []

            # Normalize
            max_score = max(aggregated.values())
            normalized = [(w, s / max_score) for w, s in aggregated.items()]
            normalized.sort(key=lambda x: x[1], reverse=True)
            return normalized[:fetch]

        except Exception as exc:
            _log.warning("[redis_wg] predict error: %s", exc)
            return None

    # ── Delegated methods ─────────────────────────────────────────────────────

    def gradient_flatness(self, context_text: str, n: int = 5) -> float:
        return self._sqlite.gradient_flatness(context_text, n=n)

    def predict_next_with_flatness(
        self, context_text: str, n: int = 5, lang: str | None = None
    ) -> tuple[list[tuple[str, float]], float]:
        preds = self.predict_next(context_text, n=n, lang=lang)
        flatness = self.gradient_flatness(context_text, n=n)
        return preds, flatness

    def bridge_words(
        self, word_a: str, word_b: str, n: int = 5
    ) -> list[tuple[str, float]]:
        return self._sqlite.bridge_words(word_a, word_b, n=n)

    def domain_exclusive(self, doc_prefix: str, n: int = 10) -> list[str]:
        return self._sqlite.domain_exclusive(doc_prefix, n=n)

    def words_by_lang(self, lang: str) -> list[str]:
        return self._sqlite.words_by_lang(lang)

    def reinforce(self, doc_id: str, boost: float = 0.1) -> None:
        if self._shadow:
            self._sqlite.reinforce(doc_id, boost=boost)

    def reinforce_text(self, text: str, boost: float = 0.05, lang: str = "en") -> None:
        if self._shadow:
            self._sqlite.reinforce_text(text, boost=boost, lang=lang)

    def save(self, path: Path) -> None:
        self._sqlite.save(path)

    @classmethod
    def load(cls, path: Path) -> "RedisWordGraph":
        raise NotImplementedError("Use make_word_graph() factory instead of load()")

    @classmethod
    def build_from_habits(cls, habits: list) -> "RedisWordGraph":
        raise NotImplementedError("Use WordGraph.build_from_habits() + redis shadow")

    def top_hubs(self, n: int = 20, lang: str | None = None) -> list[tuple[str, float]]:
        return self._sqlite.top_hubs(n=n, lang=lang)


# ── Factory ───────────────────────────────────────────────────────────────────


def make_word_graph(name: str = "word_graph", db_path: Path | None = None) -> WordGraph:
    """
    Return a WordGraph or RedisWordGraph depending on IGOR_REDIS_WORD_GRAPH_HOST.

    If the env var is set, returns a RedisWordGraph (shadow mode by default).
    Otherwise returns the standard SQLite WordGraph.
    """
    host = os.getenv("IGOR_REDIS_WORD_GRAPH_HOST", "")
    if host:
        _log.info(
            "[wg_factory] using RedisWordGraph (host=%s shadow=%s)", host, _SHADOW_MODE
        )
        return RedisWordGraph(name=name, db_path=db_path, redis_host=host)
    return WordGraph(name=name, db_path=db_path)
