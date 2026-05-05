"""
WordGraph — Postgres-backed word co-occurrence index (via db_proxy).

Two traversal directions on the same underlying weights:

  Parsing  (recognition):  score(input_text, doc_ids) → {doc_id: score}
      Given input words, which habits/memories activate most strongly?

  Generation (prediction): predict_next(context_text) → [(word, weight), ...]
      Given context words, what words most likely come next?
      Future substrate for NE incremental prediction (#50).

Language tags (#141):
  Each word node carries a language tag (e.g. "en", "fr", "nl").
  Edges cross language boundaries intentionally — co-occurrence is multilingual.
  score() and predict_next() accept an optional lang filter for targeted traversal.
  words_by_lang() and bridge_words() enable cross-language navigation.

Storage: PGDatabaseProxy against the instance Postgres DSN (same
database as the rest of the graph). No in-memory JSON load — the 191MB
JSON representation was expanding to 4-8GB Python RAM after 158 books
trained. The public API is identical to the original in-memory version;
callers unchanged. T-word-graph-docstring-sqlite (Pass-2 Area 3): the
historical "SQLite-backed" framing is retired — db_proxy is Postgres-only.

G37: name param allows two instances — recognition (listening) and generation
(speaking) — with separate graph rows and independent weight development.

Updated 2026-04-29T17:08:53Z
"""

from __future__ import annotations

import logging
import math
import os
import re
import threading
from pathlib import Path

from ..igor_base import get_logger
from ..memory.db_proxy import DatabaseProxy, make_home_proxy, make_local_proxy
from ..memory.graph_cache import GraphCache
from ..memory.pending_replies import PendingReplyStore
from ..igor_base import IgorBase

# ── Stopwords ─────────────────────────────────────────────────────────────────
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "need",
        "ought",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "up",
        "down",
        "out",
        "off",
        "over",
        "under",
        "again",
        "then",
        "once",
        "i",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "he",
        "she",
        "it",
        "they",
        "what",
        "which",
        "who",
        "this",
        "that",
        "these",
        "those",
        "and",
        "or",
        "but",
        "if",
        "while",
        "so",
        "because",
        "when",
        "where",
        "how",
        "not",
        "no",
        "nor",
        "just",
        "very",
        "also",
        "more",
        "most",
        "any",
        "all",
        # French common stopwords (intentionally small — let content words through)
        "le",
        "la",
        "les",
        "un",
        "une",
        "des",
        "du",
        "de",
        "et",
        "en",
        "est",
        "il",
        "elle",
        "ils",
        "elles",
        "je",
        "tu",
        "nous",
        "vous",
        "on",
        "que",
        "qui",
        "dans",
        "sur",
        "par",
        "avec",
        "pour",
        "au",
        "aux",
        # Dutch
        "de",
        "het",
        "een",
        "van",
        "in",
        "is",
        "dat",
        "op",
        "te",
        "zijn",
        "er",
        "maar",
        "om",
        "dit",
        "die",
        "ook",
        "bij",
        "als",
        "dan",
        "nog",
    }
)


# ── Lemmatizer (English only — WordNet covers the primary training corpus) ─────
# Lazy-init so import cost is zero for processes that never call tokenize().
_wn_lemmatizer = None
_lemma_cache: dict[str, str] = {}


def _lemmatize_en(word: str) -> str:
    """
    Reduce an English word to its canonical lemma form.
    Tries verb POS first (run/ran/running → run), then noun (memories → memory).
    Results are cached in a module-level dict; typical cache hit rate >99% after
    a few thousand unique tokens.
    """
    global _wn_lemmatizer
    cached = _lemma_cache.get(word)
    if cached is not None:
        return cached
    if _wn_lemmatizer is None:
        try:
            from nltk.stem import WordNetLemmatizer

            _wn_lemmatizer = WordNetLemmatizer()
        except Exception:
            # nltk not available — fall through to identity
            _lemma_cache[word] = word
            return word
    v = _wn_lemmatizer.lemmatize(word, "v")
    result = v if v != word else _wn_lemmatizer.lemmatize(word, "n")
    _lemma_cache[word] = result
    return result


def tokenize(text: str, lang: str = "en") -> list[str]:
    """
    Lowercase, extract word tokens, remove stopwords and single chars.
    English tokens are lemmatized to canonical form (run/ran/running → run).

    Handles Unicode Latin characters (accented French, Dutch, Spanish, German, etc.)
    via an extended character class. Underscores preserved for compound tokens.
    """
    # Unicode Latin Extended (U+00C0–U+024F) covers most Western European languages.
    words = re.findall(r"[a-z\u00c0-\u024f0-9_]+", text.lower())
    tokens = [w for w in words if w not in _STOPWORDS and len(w) > 1]
    if lang == "en":
        return [_lemmatize_en(w) for w in tokens]
    return tokens


def tokenize_with_bigrams(text: str, lang: str = "en") -> list[str]:
    """
    Like tokenize() but also yields adjacent-word bigrams as chunk tokens.
    e.g. "to be or not to be" → ["not", "be__be"] (stopwords stripped first,
    bigrams formed from the remaining sequence).

    Bigrams capture bound phrases ("word_graph", "habit_compiler", "new_york")
    at the chunk level — one step above individual words.
    Bigram tokens use __ separator to avoid collision with plain words.
    """
    words = tokenize(text, lang=lang)
    tokens = list(words)
    for a, b in zip(words, words[1:]):
        tokens.append(f"{a}__{b}")
    return tokens


# ── Backward-compat proxy (used by main.py and dashboard/terminal.py) ─────────


class _WordDocProxy:
    """
    Proxy for _word_to_ids that supports len() and bool() without loading
    the full word→doc mapping into memory.
    """

    __slots__ = ("_db",)

    def __init__(self, db: DatabaseProxy) -> None:
        self._db = db

    def __len__(self) -> int:
        # Read word_count from wg_meta (maintained incrementally in WordGraph.index()).
        # Falls back to COUNT(DISTINCT word) once on first call, then caches in wg_meta.
        with self._db() as conn:
            row = conn.execute(
                "SELECT value FROM wg_meta WHERE key = 'word_count'"
            ).fetchone()
            if row is not None:
                return int(row[0])
            # First call ever: compute and cache (one-time cost per DB lifetime)
            n = conn.execute("SELECT COUNT(DISTINCT word) FROM wg_word_docs").fetchone()
            count = n[0] if n else 0
            conn.execute(
                "INSERT INTO wg_meta (key, value) VALUES ('word_count', %s)"
                " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (str(count),),
            )
            return count

    def __bool__(self) -> bool:
        with self._db() as conn:
            row = conn.execute("SELECT 1 FROM wg_word_docs LIMIT 1").fetchone()
        return row is not None


# ── WordGraph ─────────────────────────────────────────────────────────────────
# Tables (clan.wg_word_docs, clan.wg_cooccur, clan.wg_word_lang, clan.wg_idf,
# clan.wg_meta, clan.wg_edges, clan.wg_lemma_map) are owned by cortex m050
# migrations. WordGraph reads/writes them via make_home_proxy.


class WordGraph(IgorBase):
    """
    Word graph with language tags on nodes (#141). Backed by Postgres via
    db_proxy (the "SQLite-backed" framing is retired; db_proxy is
    Postgres-only after D328+).

    Storage tables:
      wg_word_docs  : word, doc_id, weight  — parsing direction
      wg_cooccur    : word_a, word_b, score  — generation direction
      wg_word_lang  : word, lang             — language of each node
      wg_idf        : word, score            — IDF weights
      wg_meta       : key, value             — doc_count etc.

    Public API identical to original in-memory version; callers unchanged.
    G37: name param → separate DB files for recognition and generation graphs.
    """

    # predict_next LRU cache: (words_tuple, lang, fetch_n) → [(word, score), ...]
    # Cleared on every index() call. Max 512 entries; evict half when full.
    _PREDICT_CACHE_MAX = 512

    def __init__(self, name: str = "word_graph") -> None:
        super().__init__()
        self.name = name
        self._lock = threading.RLock()
        # Postgres-only since D-sqlite-removal (T-sqlite-out-word-graph-db).
        # Tables clan.wg_* are owned by cortex m050 migrations — we don't
        # double-declare them here.
        self._db = make_home_proxy()
        self._local_db = make_local_proxy()
        # Partial index for bigram filter (strpos = 0 → not a bigram). Speeds
        # up hot_nodes(words_only=True) and bridge_words() on 29M-row table.
        # Could move into a cortex migration; left here as the only caller.
        from ..memory.db_proxy import PGDatabaseProxy

        if isinstance(self._db, PGDatabaseProxy):
            with self._db() as conn:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_wgc_a_unigram"
                    " ON wg_cooccur(word_a) WHERE strpos(word_a, '__') = 0"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_wgc_b_unigram"
                    " ON wg_cooccur(word_b) WHERE strpos(word_b, '__') = 0"
                )
        # D126 Step 3: PendingReplyStore — resilience queue for failed home DB writes
        self._pending = PendingReplyStore(self._local_db, self._db)
        # D126 Step 2: GraphCache — Redis hot-cache for wg_cooccur; gates on IGOR_REDIS_URL
        self._cache = GraphCache(self._db, self._local_db, pending_store=self._pending)
        # G-WG2: predict_next cache — avoid re-querying wg_edges on repeated context
        self._predict_cache: dict[tuple, list] = {}
        # G-WG3: doc_count write batching — flush every N docs instead of every index()
        self._pending_doc_count: int = 0
        self._DOC_FLUSH_EVERY: int = 10
        # T-wg-meta-upsert-latency: word_count write batching — same pattern as
        # doc_count above. Every index() call was upserting the hot 'word_count'
        # row, hitting up to 5.8s worst-case on row-lock contention. Buffer in
        # memory, flush every N new words or on shutdown/build_idf.
        self._pending_word_count: int = 0
        self._WORD_FLUSH_EVERY: int = 50

    # ── Backward-compat properties ─────────────────────────────────────────────

    @property
    def _word_to_ids(self) -> _WordDocProxy:
        """Proxy for len() and bool() checks in main.py / terminal.py."""
        return _WordDocProxy(self._db)

    @property
    def _doc_count(self) -> int:
        with self._db() as conn:
            row = conn.execute(
                "SELECT value FROM wg_meta WHERE key = 'doc_count'"
            ).fetchone()
        # G-WG3: include unflushed pending count so callers see live value
        return (int(row[0]) if row else 0) + self._pending_doc_count

    def _inc_doc_count(self, conn) -> None:
        # G-WG3: batch doc_count writes — accumulate in memory, flush every N docs
        # instead of one SQLite write transaction per index() call (was 116x × 320ms)
        self._pending_doc_count += 1
        if self._pending_doc_count >= self._DOC_FLUSH_EVERY:
            conn.execute(
                "INSERT INTO wg_meta (key, value) VALUES ('doc_count', %s)"
                " ON CONFLICT(key) DO UPDATE"
                " SET value = CAST(CAST(wg_meta.value AS INTEGER) + %s AS TEXT)",
                (str(self._pending_doc_count), self._pending_doc_count),
            )
            self._pending_doc_count = 0

    def flush_doc_count(self) -> None:
        """Flush any pending doc_count increment to DB. Call on shutdown or build_idf."""
        if self._pending_doc_count > 0:
            with self._db() as conn:
                conn.execute(
                    "INSERT INTO wg_meta (key, value) VALUES ('doc_count', %s)"
                    " ON CONFLICT(key) DO UPDATE"
                    " SET value = CAST(CAST(wg_meta.value AS INTEGER) + %s AS TEXT)",
                    (str(self._pending_doc_count), self._pending_doc_count),
                )
            self._pending_doc_count = 0

    def _inc_word_count(self, conn, delta: int) -> None:
        """T-wg-meta-upsert-latency: batch word_count writes. Matches G-WG3 doc_count shape."""
        if delta <= 0:
            return
        self._pending_word_count += delta
        if self._pending_word_count >= self._WORD_FLUSH_EVERY:
            conn.execute(
                "INSERT INTO wg_meta (key, value) VALUES ('word_count', %s)"
                " ON CONFLICT(key) DO UPDATE"
                " SET value = CAST(CAST(wg_meta.value AS INTEGER) + %s AS TEXT)",
                (str(self._pending_word_count), self._pending_word_count),
            )
            self._pending_word_count = 0

    def flush_word_count(self) -> None:
        """Flush any pending word_count increment to DB. Call on shutdown or build_idf."""
        if self._pending_word_count > 0:
            with self._db() as conn:
                conn.execute(
                    "INSERT INTO wg_meta (key, value) VALUES ('word_count', %s)"
                    " ON CONFLICT(key) DO UPDATE"
                    " SET value = CAST(CAST(wg_meta.value AS INTEGER) + %s AS TEXT)",
                    (str(self._pending_word_count), self._pending_word_count),
                )
            self._pending_word_count = 0

    # ── Indexing ───────────────────────────────────────────────────────────────

    def index(
        self, doc_id: str, text: str, weight: float = 1.0, lang: str = "en"
    ) -> None:
        """
        Index a document so its words and bigram chunks participate in scoring.

        lang: BCP-47 language tag for the source text (e.g. "en", "fr", "nl").
        Words already present with a different lang keep their existing tag;
        new words are tagged with lang. Cross-language co-occurrence edges are
        formed intentionally — this is the feature, not a bug.
        """
        tokens = tokenize_with_bigrams(text, lang=lang)
        if not tokens:
            return
        unique = list(dict.fromkeys(tokens))  # preserve order, dedupe

        with self._lock:
            with self._db() as conn:
                # word → doc weights (max of existing vs new)
                conn.executemany(
                    """
                    INSERT INTO wg_word_docs (word, doc_id, weight) VALUES (%s, %s, %s)
                    ON CONFLICT(word, doc_id)
                    DO UPDATE SET weight = CASE WHEN wg_word_docs.weight > excluded.weight THEN wg_word_docs.weight ELSE excluded.weight END
                """,
                    [(w, doc_id, weight) for w in unique],
                )

                # language tags (first writer wins)
                # T-wg-meta-upsert-latency: batched via _inc_word_count; count
                # actual inserts via RETURNING (replaces SQLite SELECT changes()).
                if unique:
                    _ph = ", ".join(["(%s, %s)"] * len(unique))
                    _params = [v for w in unique for v in (w, lang)]
                    _inserted = conn.execute(
                        f"INSERT INTO wg_word_lang (word, lang) VALUES {_ph}"
                        " ON CONFLICT (word) DO NOTHING RETURNING word",
                        _params,
                    ).fetchall()
                    _new_words = len(_inserted)
                else:
                    _new_words = 0
                self._inc_word_count(conn, _new_words)

                self._inc_doc_count(conn)

    def build_idf(self) -> None:
        """Compute and persist IDF weights. Call once after all index() calls."""
        self.flush_doc_count()  # G-WG3: ensure pending count is flushed before IDF uses it
        self.flush_word_count()  # T-wg-meta-upsert-latency: same pattern for word_count
        n = max(self._doc_count, 1)
        with self._lock:
            with self._db() as conn:
                rows = conn.execute(
                    "SELECT word, COUNT(DISTINCT doc_id) FROM wg_word_docs GROUP BY word"
                ).fetchall()
                conn.executemany(
                    "INSERT INTO wg_idf (word, score) VALUES (%s, %s)"
                    " ON CONFLICT (word) DO UPDATE SET score = EXCLUDED.score",
                    [(w, math.log(n / max(df, 1))) for w, df in rows],
                )

    # ── Parsing direction ──────────────────────────────────────────────────────

    def score(
        self, input_text: str, doc_ids: list[str], lang: str | None = None
    ) -> dict[str, float]:
        """
        Score each doc_id by TF-IDF word overlap with input_text.
        Returns {doc_id: score} normalised to [0, 1].

        lang: if specified, only words tagged with that language contribute.
              None (default) uses all words — cross-language scoring.
        """
        words = list(tokenize_with_bigrams(input_text))
        if not words or not doc_ids:
            return {}

        with self._db() as conn:
            if lang is not None:
                ph = ",".join(["%s"] * len(words))
                lang_rows = conn.execute(
                    f"SELECT word FROM wg_word_lang WHERE word IN ({ph}) AND lang = %s",
                    words + [lang],
                ).fetchall()
                words = [r[0] for r in lang_rows]
                if not words:
                    return {}

            w_ph = ",".join(["%s"] * len(words))
            doc_ph = ",".join(["%s"] * len(doc_ids))
            rows = conn.execute(
                f"""
                SELECT wd.doc_id, SUM(wd.weight * COALESCE(i.score, 1.0)) AS total
                FROM wg_word_docs wd
                LEFT JOIN wg_idf i ON wd.word = i.word
                WHERE wd.word IN ({w_ph}) AND wd.doc_id IN ({doc_ph})
                GROUP BY wd.doc_id
            """,
                words + doc_ids,
            ).fetchall()

        if not rows:
            return {}
        raw = {r[0]: r[1] for r in rows}
        max_score = max(raw.values())
        return {k: v / max_score for k, v in raw.items()}

    # ── Generation direction ───────────────────────────────────────────────────

    def predict_next(
        self,
        context_text: str,
        n: int = 5,
        lang: str | None = None,
        milieu_state: dict | None = None,
    ) -> list[tuple[str, float]]:
        """
        Given context text, return top-N co-occurring words by accumulated weight.

        lang: if specified, only return predictions tagged with that language.
              None (default) returns across all languages — enables code-switching.

        milieu_state: optional dict with 'arousal' key in [0.0, 1.0].
            High arousal steepens the gradient — top candidates pull harder relative
            to weaker ones (sharpened softmax-like effect). Low arousal flattens it,
            producing more diffuse, exploratory predictions.
            G37: milieu tilts the gradient field without rewriting it.
        """
        words = list(tokenize_with_bigrams(context_text))
        if not words:
            return []

        w_ph = ",".join(["%s"] * len(words))
        fetch = n * 3 if milieu_state else n  # fetch extra when milieu tilt applied

        # G-WG2: LRU-style cache — skip DB aggregation on repeated context
        # Key excludes milieu_state (tilt applied post-fetch, not in SQL)
        _cache_key = (tuple(sorted(words)), lang, fetch)
        if _cache_key in self._predict_cache:
            rows = self._predict_cache[_cache_key]
        else:
            rows = None

        if rows is None:
            if lang is not None:
                # Lang-filtered: join wg_word_lang to restrict results by language
                with self._db() as conn:
                    rows = conn.execute(
                        f"""
                        SELECT e.word_b, SUM(e.similarity) AS total
                        FROM wg_edges e
                        JOIN wg_word_lang l ON e.word_b = l.word
                        WHERE e.word_a IN ({w_ph}) AND l.lang = %s
                        GROUP BY e.word_b
                        ORDER BY total DESC
                        LIMIT %s
                    """,
                        words + [lang, fetch],
                    ).fetchall()
            else:
                # wg_edges is ~1.5M rows — query directly, no Redis needed
                with self._db() as conn:
                    rows = conn.execute(
                        f"""
                        SELECT word_b, SUM(similarity) AS total
                        FROM wg_edges
                        WHERE word_a IN ({w_ph})
                        GROUP BY word_b
                        ORDER BY total DESC
                        LIMIT %s
                    """,
                        words + [fetch],
                    ).fetchall()
            # G-WG2: store in cache; evict half when full
            if len(self._predict_cache) >= self._PREDICT_CACHE_MAX:
                evict = list(self._predict_cache.keys())[: self._PREDICT_CACHE_MAX // 2]
                for k in evict:
                    del self._predict_cache[k]
            self._predict_cache[_cache_key] = rows

        if not rows:
            return []

        counts = {r[0]: float(r[1]) for r in rows}

        # G37: milieu tilt — arousal sharpens gradient (temperature-like)
        if milieu_state is not None:
            arousal = float(milieu_state.get("arousal", 0.5))
            exponent = 0.5 + arousal * 1.5  # [0,1] → [0.5, 2.0]
            counts = {w: v**exponent for w, v in counts.items()}

        return sorted(counts.items(), key=lambda x: x[1], reverse=True)[:n]

    def gradient_flatness(self, context_text: str, n: int = 5) -> float:
        """
        Returns a [0.0, 1.0] flatness score for the current prediction gradient.

        0.0 = steep gradient (strong top prediction — generation has clear direction)
        1.0 = flat gradient (no strong prediction — natural stopping point)

        G37: reply termination condition. When flatness exceeds threshold (~0.8),
        the gradient has nothing more to push — silence is the right answer.
        The value returned is 1 - normalised_top_weight.
        """
        top = self.predict_next(context_text, n=n)
        if not top:
            return 1.0
        max_weight = top[0][1]
        if max_weight <= 0:
            return 1.0
        # normalise against rough expected max (empirical: ~50 co-occurrences typical)
        normalised = min(
            max_weight / 1.0, 1.0
        )  # TODO: tune threshold post-wg_edges cutover
        return 1.0 - normalised

    def predict_next_with_flatness(
        self, context_text: str, n: int = 5
    ) -> tuple[list[tuple[str, float]], float]:
        """
        Convenience wrapper: returns (predictions, flatness) in one call.

        D072: generation graph vigilance gate.
        flatness=0.0 → steep (strong dominant prediction = REFLEXIVE → inhibit).
        flatness=1.0 → flat (no dominant prediction = novel/uncertain).
        """
        predictions = self.predict_next(context_text, n=n)
        if not predictions:
            return [], 1.0
        max_weight = predictions[0][1]
        if max_weight <= 0:
            return predictions, 1.0
        normalised = min(
            max_weight / 1.0, 1.0
        )  # TODO: tune threshold post-wg_edges cutover
        return predictions, 1.0 - normalised

    # ── D233: spreading activation ─────────────────────────────────────────────

    def spread_from_words(
        self,
        seed_words: dict,
        hop_decay: float = 0.6,
        depth: int = 2,
        max_frontier: int = 300,
    ) -> dict:
        """D233: Spread activation from seed words through wg_edges.

        Returns dict[word, activation_score] with multi-source summed activations.
        hop_decay: multiplier applied per hop (default 0.6).
        depth: number of hops to propagate.
        max_frontier: cap on the number of words carried into each hop. Keeps
            the wg_edges IN-clause from exploding on large/dense graphs. Top-N
            by activation score are kept; the rest are still merged into scores
            but dropped from the next hop's query.

        Used by cortex.spreading_activation() as the word-graph layer.
        """
        scores: dict = dict(seed_words)
        current_frontier = dict(seed_words)
        for _ in range(depth):
            if not current_frontier:
                break
            if len(current_frontier) > max_frontier:
                current_frontier = dict(
                    sorted(
                        current_frontier.items(), key=lambda kv: kv[1], reverse=True
                    )[:max_frontier]
                )
            words_list = list(current_frontier.keys())
            ph = ",".join(["%s"] * len(words_list))
            try:
                with self._db() as conn:
                    rows = conn.execute(
                        f"SELECT word_a, word_b, similarity FROM wg_edges"
                        f" WHERE word_a IN ({ph})",
                        words_list,
                    ).fetchall()
            except Exception as _e:
                get_logger(__name__).warning(
                    "bare except in wild_igor/igor/cognition/word_graph.py spread_from_words: %s",
                    _e,
                )
                break
            next_frontier: dict = {}
            for row in rows:
                word_a, word_b, sim = row[0], row[1], float(row[2])
                spread = current_frontier.get(word_a, 0.0) * sim * hop_decay
                if spread > 0:
                    next_frontier[word_b] = next_frontier.get(word_b, 0.0) + spread
            for w, s in next_frontier.items():
                scores[w] = scores.get(w, 0.0) + s
            current_frontier = next_frontier
        return scores

    def words_to_doc_ids(self, word_scores: dict, limit: int = 50) -> dict:
        """D233: Bridge — map word activation scores to doc_id activations.

        Queries wg_word_docs for top-`limit` words by activation score.
        Returns dict[doc_id, float] with activations summed across all words
        that link to a given doc_id.

        Used by cortex.spreading_activation() to bridge the word-graph layer
        into the memory graph (content index bridge).
        """
        if not word_scores:
            return {}
        top_words = sorted(word_scores, key=word_scores.__getitem__, reverse=True)[
            :limit
        ]
        ph = ",".join(["%s"] * len(top_words))
        try:
            with self._db() as conn:
                rows = conn.execute(
                    f"SELECT word, doc_id, weight FROM wg_word_docs WHERE word IN ({ph})",
                    top_words,
                ).fetchall()
        except Exception as _e:
            get_logger(__name__).warning(
                "bare except in wild_igor/igor/cognition/word_graph.py words_to_doc_ids: %s",
                _e,
            )
            return {}
        doc_scores: dict = {}
        for row in rows:
            word, doc_id, weight = row[0], row[1], float(row[2])
            activation = word_scores.get(word, 0.0) * weight
            if activation > 0:
                doc_scores[doc_id] = doc_scores.get(doc_id, 0.0) + activation
        return doc_scores

    # ── Graph analysis ─────────────────────────────────────────────────────────

    def top_hubs(
        self, n: int = 10, words_only: bool = True, lang: str | None = None
    ) -> list[tuple[str, int]]:
        """
        Return the N most-connected words by co-occurrence neighbour count.
        words_only=True skips bigram tokens (a__b) to keep results readable.
        lang: optional filter to a specific language.
        """
        conditions: list[str] = []
        params: list = []
        join = ""

        if words_only:
            conditions.append("strpos(c.word_a, '__') = 0")
        if lang is not None:
            join = " JOIN wg_word_lang l ON c.word_a = l.word"
            conditions.append("l.lang = %s")
            params.append(lang)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(n)

        with self._db() as conn:
            rows = conn.execute(
                f"SELECT c.word_a, COUNT(*) AS degree"
                f" FROM wg_cooccur c{join}{where}"
                f" GROUP BY c.word_a ORDER BY degree DESC LIMIT %s",
                params,
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def bridge_words(
        self, word_a: str, word_b: str, n: int = 10
    ) -> list[tuple[str, float]]:
        """
        Find words that co-occur with BOTH word_a and word_b — the connective
        tissue between two concepts. Ranked by combined co-occurrence weight.
        Works across language boundaries (cross-language bridges are valid).
        Returns [] if either word is not in the graph.
        """
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT ca.word_b, ca.score + cb.score AS combined
                FROM wg_cooccur ca
                JOIN wg_cooccur cb ON ca.word_b = cb.word_b
                WHERE ca.word_a = %s AND cb.word_a = %s
                  AND strpos(ca.word_b, '__') = 0
                ORDER BY combined DESC
                LIMIT %s
            """,
                (word_a.lower(), word_b.lower(), n),
            ).fetchall()
        return [(r[0], float(r[1])) for r in rows]

    def domain_exclusive(self, doc_prefix: str, n: int = 10) -> list[str]:
        """
        Find words that appear ONLY in docs whose id starts with doc_prefix.
        Useful for isolating specialised vocabulary (e.g. 'hamlet_' or 'neuro_').
        """
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT word, SUM(weight) AS total_weight
                FROM wg_word_docs
                WHERE strpos(word, '__') = 0
                GROUP BY word
                HAVING SUM(CASE WHEN doc_id NOT LIKE %s THEN 1 ELSE 0 END) = 0
                ORDER BY total_weight DESC
                LIMIT %s
            """,
                (doc_prefix + "%", n),
            ).fetchall()
        return [r[0] for r in rows]

    def words_by_lang(self, lang: str) -> list[str]:
        """
        Return all word nodes tagged with the given language.
        Bigram tokens (w1__w2) are excluded — unigrams only.
        """
        with self._db() as conn:
            rows = conn.execute(
                "SELECT word FROM wg_word_lang WHERE lang = %s AND strpos(word, '__') = 0",
                (lang,),
            ).fetchall()
        return [r[0] for r in rows]

    # ── Learning ───────────────────────────────────────────────────────────────

    def reinforce(self, doc_id: str, boost: float = 0.1) -> None:
        """
        Boost word weights for a document that just activated (e.g. habit fired).
        Experiences gradually reshape word weights — the learning loop.
        Capped at 2.0 to prevent runaway dominance.
        """
        with self._lock:
            with self._db() as conn:
                conn.execute(
                    "UPDATE wg_word_docs SET weight = CASE WHEN weight + %s > 2.0 THEN 2.0 ELSE weight + %s END WHERE doc_id = %s",
                    (boost, boost, doc_id),
                )

    def surprise_scale(self, flatness: float) -> float:
        """
        #338: Sparse reward — scale reinforcement by prediction surprise.

        flatness (from gradient_flatness) is [0,1]:
          0.0 = expected/routine input (steep gradient) → scale = 1.0, no bonus
          1.0 = novel/surprising input (flat gradient)  → scale = 1 + MULTIPLIER

        Gate: IGOR_SURPRISE_REWARD_ENABLED (default true — T-surprise-reward-enable).
        Returns 1.0 when gate is off — caller multiplies boost by this unchanged.
        """
        import os as _os

        if _os.getenv("IGOR_SURPRISE_REWARD_ENABLED", "true").lower() not in (
            "1",
            "true",
            "yes",
        ):
            return 1.0
        multiplier = float(_os.getenv("IGOR_SURPRISE_MULTIPLIER", "2.0"))
        return 1.0 + max(0.0, min(1.0, flatness)) * multiplier

    # G-WG4: cap unique tokens in reinforce_text to bound the O(n²) pair count.
    # 40 unique tokens → 1560 pairs (manageable); 200 tokens → 39800 pairs (slow).
    _REINFORCE_TOKEN_CAP = 40

    def reinforce_text(self, text: str, boost: float = 0.05, lang: str = "en") -> None:
        """
        Boost co-occurrence edges for words in text — the comprehension signal loop.

        G37: called on the generation graph when we receive a positive comprehension
        signal (the other person heard what we meant). Strengthens the word paths
        that produced a well-received reply. Opposite of index() which sets initial
        weights — this nudges weights up based on observed success.

        boost: small positive delta per edge (default 0.05 — 20× smaller than
               reinforce() doc boost, because text-level signals are coarser).
        Capped at 2.0 per edge to prevent runaway dominance.

        G-WG4: unique token list capped at _REINFORCE_TOKEN_CAP to prevent
        O(n²) pair explosion on long replies (200 tokens → 39800 pairs → slow).
        """
        tokens = tokenize_with_bigrams(text, lang=lang)
        unique = list(dict.fromkeys(tokens))
        if len(unique) < 2:
            return
        # G-WG4: cap tokens to bound pair count (n*(n-1)) at a safe level
        if len(unique) > self._REINFORCE_TOKEN_CAP:
            unique = unique[: self._REINFORCE_TOKEN_CAP]
        with self._lock:
            with self._db() as conn:
                conn.executemany(
                    """
                    UPDATE wg_cooccur SET score = CASE WHEN score + %s > 2.0 THEN 2.0 ELSE score + %s END
                    WHERE word_a = %s AND word_b = %s
                """,
                    [(boost, boost, w, w2) for w in unique for w2 in unique if w != w2],
                )

    # ── Persistence ────────────────────────────────────────────────────────────
    # Postgres writes inside index() / build_idf() are synchronous via the
    # home_db proxy. No save() / load() needed — the data is durable as soon
    # as a transaction commits.

    @classmethod
    def build_from_habits(cls, habits: list) -> "WordGraph":
        """
        Build a fresh WordGraph from a list of Memory objects (habits).
        Indexes both the trigger phrase and the narrative for each habit.
        Lang tag inferred from habit metadata["lang"] if present, else "en".
        """
        g = cls()
        for h in habits:
            trigger = h.metadata.get("trigger", "") if h.metadata else ""
            lang = (h.metadata or {}).get("lang", "en")  # #141
            if trigger:
                g.index(h.id, trigger, weight=2.0, lang=lang)
            if h.narrative:
                g.index(h.id, h.narrative, weight=1.0, lang=lang)
        g.build_idf()
        return g
