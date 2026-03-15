"""
WordGraph — SQLite-backed word co-occurrence index.

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

Storage: SQLite (~/.TheIgors/{name}.db). No in-memory JSON load — the 191MB
JSON representation was expanding to 4-8GB Python RAM after 158 books trained.
The public API is identical to the original in-memory version; callers unchanged.

G37: name param allows two instances — recognition (listening) and generation
(speaking) — with separate DB files and independent weight development.
"""

from __future__ import annotations

import math
import re
import sqlite3
import threading
from pathlib import Path

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


def tokenize(text: str, lang: str = "en") -> list[str]:
    """
    Lowercase, extract word tokens, remove stopwords and single chars.

    Handles Unicode Latin characters (accented French, Dutch, Spanish, German, etc.)
    via an extended character class. Underscores preserved for compound tokens.
    """
    # Unicode Latin Extended (U+00C0–U+024F) covers most Western European languages.
    words = re.findall(r"[a-z\u00c0-\u024f0-9_]+", text.lower())
    return [w for w in words if w not in _STOPWORDS and len(w) > 1]


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

    __slots__ = ("_conn",)

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def __len__(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT word) FROM wg_word_docs"
        ).fetchone()
        return row[0] if row else 0

    def __bool__(self) -> bool:
        row = self._conn.execute("SELECT 1 FROM wg_word_docs LIMIT 1").fetchone()
        return row is not None


# ── WordGraph ─────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wg_word_docs (
    word   TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY (word, doc_id)
);
CREATE INDEX IF NOT EXISTS idx_wgd_doc  ON wg_word_docs(doc_id);

CREATE TABLE IF NOT EXISTS wg_cooccur (
    word_a TEXT NOT NULL,
    word_b TEXT NOT NULL,
    score  REAL NOT NULL DEFAULT 0.0,
    PRIMARY KEY (word_a, word_b)
);
CREATE INDEX IF NOT EXISTS idx_wgc_a ON wg_cooccur(word_a);

CREATE TABLE IF NOT EXISTS wg_word_lang (
    word TEXT PRIMARY KEY,
    lang TEXT NOT NULL DEFAULT 'en'
);

CREATE TABLE IF NOT EXISTS wg_idf (
    word  TEXT PRIMARY KEY,
    score REAL NOT NULL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS wg_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class WordGraph:
    """
    SQLite-backed word graph with language tags on nodes (#141).

    Storage tables:
      wg_word_docs  : word, doc_id, weight  — parsing direction
      wg_cooccur    : word_a, word_b, score  — generation direction
      wg_word_lang  : word, lang             — language of each node
      wg_idf        : word, score            — IDF weights
      wg_meta       : key, value             — doc_count etc.

    Public API identical to original in-memory version; callers unchanged.
    G37: name param → separate DB files for recognition and generation graphs.
    """

    def __init__(self, name: str = "word_graph", db_path: Path | None = None) -> None:
        self.name = name
        self._db_path = db_path or default_cache_path(name)
        self._lock = threading.RLock()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA cache_size=-16000")  # 16 MB page cache
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ── Backward-compat properties ─────────────────────────────────────────────

    @property
    def _word_to_ids(self) -> _WordDocProxy:
        """Proxy for len() and bool() checks in main.py / terminal.py."""
        return _WordDocProxy(self._conn)

    @property
    def _doc_count(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM wg_meta WHERE key = 'doc_count'"
        ).fetchone()
        return int(row[0]) if row else 0

    def _inc_doc_count(self) -> None:
        self._conn.execute("""
            INSERT INTO wg_meta (key, value) VALUES ('doc_count', '1')
            ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)
        """)

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
            # word → doc weights (max of existing vs new)
            self._conn.executemany(
                """
                INSERT INTO wg_word_docs (word, doc_id, weight) VALUES (?, ?, ?)
                ON CONFLICT(word, doc_id)
                DO UPDATE SET weight = MAX(weight, excluded.weight)
            """,
                [(w, doc_id, weight) for w in unique],
            )

            # language tags (first writer wins)
            self._conn.executemany(
                "INSERT OR IGNORE INTO wg_word_lang (word, lang) VALUES (?, ?)",
                [(w, lang) for w in unique],
            )

            # co-occurrence edges (accumulate).
            # Only pair plain words (no bigrams) and cap at 50 to prevent N²
            # list explosion: a 200-token paragraph generates 40K pairs,
            # blowing 1-2 GB RAM per book during bulk training.
            _cooccur_words = [w for w in unique if "__" not in w][:50]
            self._conn.executemany(
                """
                INSERT INTO wg_cooccur (word_a, word_b, score) VALUES (?, ?, 1.0)
                ON CONFLICT(word_a, word_b)
                DO UPDATE SET score = score + 1.0
            """,
                [(w, w2) for w in _cooccur_words for w2 in _cooccur_words if w != w2],
            )

            self._inc_doc_count()
            self._conn.commit()

    def build_idf(self) -> None:
        """Compute and persist IDF weights. Call once after all index() calls."""
        n = max(self._doc_count, 1)
        with self._lock:
            rows = self._conn.execute(
                "SELECT word, COUNT(DISTINCT doc_id) FROM wg_word_docs GROUP BY word"
            ).fetchall()
            self._conn.executemany(
                "INSERT OR REPLACE INTO wg_idf (word, score) VALUES (?, ?)",
                [(w, math.log(n / max(df, 1))) for w, df in rows],
            )
            self._conn.commit()

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

        if lang is not None:
            ph = ",".join("?" * len(words))
            lang_rows = self._conn.execute(
                f"SELECT word FROM wg_word_lang WHERE word IN ({ph}) AND lang = ?",
                words + [lang],
            ).fetchall()
            words = [r[0] for r in lang_rows]
            if not words:
                return {}

        w_ph = ",".join("?" * len(words))
        doc_ph = ",".join("?" * len(doc_ids))
        rows = self._conn.execute(
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

        w_ph = ",".join("?" * len(words))
        fetch = n * 3 if milieu_state else n  # fetch extra when milieu tilt applied

        if lang is not None:
            rows = self._conn.execute(
                f"""
                SELECT c.word_b, SUM(c.score) AS total
                FROM wg_cooccur c
                JOIN wg_word_lang l ON c.word_b = l.word
                WHERE c.word_a IN ({w_ph}) AND l.lang = ?
                GROUP BY c.word_b
                ORDER BY total DESC
                LIMIT ?
            """,
                words + [lang, fetch],
            ).fetchall()
        else:
            rows = self._conn.execute(
                f"""
                SELECT word_b, SUM(score) AS total
                FROM wg_cooccur
                WHERE word_a IN ({w_ph})
                GROUP BY word_b
                ORDER BY total DESC
                LIMIT ?
            """,
                words + [fetch],
            ).fetchall()

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
        normalised = min(max_weight / 50.0, 1.0)
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
        normalised = min(max_weight / 50.0, 1.0)
        return predictions, 1.0 - normalised

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
            conditions.append("INSTR(c.word_a, '__') = 0")
        if lang is not None:
            join = " JOIN wg_word_lang l ON c.word_a = l.word"
            conditions.append("l.lang = ?")
            params.append(lang)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(n)

        rows = self._conn.execute(
            f"SELECT c.word_a, COUNT(*) AS degree"
            f" FROM wg_cooccur c{join}{where}"
            f" GROUP BY c.word_a ORDER BY degree DESC LIMIT ?",
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
        rows = self._conn.execute(
            """
            SELECT ca.word_b, ca.score + cb.score AS combined
            FROM wg_cooccur ca
            JOIN wg_cooccur cb ON ca.word_b = cb.word_b
            WHERE ca.word_a = ? AND cb.word_a = ?
              AND INSTR(ca.word_b, '__') = 0
            ORDER BY combined DESC
            LIMIT ?
        """,
            (word_a.lower(), word_b.lower(), n),
        ).fetchall()
        return [(r[0], float(r[1])) for r in rows]

    def domain_exclusive(self, doc_prefix: str, n: int = 10) -> list[str]:
        """
        Find words that appear ONLY in docs whose id starts with doc_prefix.
        Useful for isolating specialised vocabulary (e.g. 'hamlet_' or 'neuro_').
        """
        rows = self._conn.execute(
            """
            SELECT word, SUM(weight) AS total_weight
            FROM wg_word_docs
            WHERE INSTR(word, '__') = 0
            GROUP BY word
            HAVING SUM(CASE WHEN doc_id NOT LIKE ? THEN 1 ELSE 0 END) = 0
            ORDER BY total_weight DESC
            LIMIT ?
        """,
            (doc_prefix + "%", n),
        ).fetchall()
        return [r[0] for r in rows]

    def words_by_lang(self, lang: str) -> list[str]:
        """
        Return all word nodes tagged with the given language.
        Bigram tokens (w1__w2) are excluded — unigrams only.
        """
        rows = self._conn.execute(
            "SELECT word FROM wg_word_lang WHERE lang = ? AND INSTR(word, '__') = 0",
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
            self._conn.execute(
                "UPDATE wg_word_docs SET weight = MIN(weight + ?, 2.0) WHERE doc_id = ?",
                (boost, doc_id),
            )
            self._conn.commit()

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
        """
        tokens = tokenize_with_bigrams(text, lang=lang)
        unique = list(dict.fromkeys(tokens))
        if len(unique) < 2:
            return
        with self._lock:
            self._conn.executemany(
                """
                UPDATE wg_cooccur SET score = MIN(score + ?, 2.0)
                WHERE word_a = ? AND word_b = ?
            """,
                [(boost, w, w2) for w in unique for w2 in unique if w != w2],
            )
            self._conn.commit()

    # ── Persistence ────────────────────────────────────────────────────────────

    def save(self, path: Path) -> None:
        """
        Flush WAL to main DB file. Data is already persisted in SQLite so this
        is a lightweight checkpoint rather than a full serialise. The path arg
        is ignored (kept for API compatibility with callers that pass cache_path).
        """
        try:
            self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except Exception:
            pass

    @classmethod
    def load(cls, path: Path) -> "WordGraph":
        """
        Open (or create) the SQLite word graph at the given path.
        Returns an empty graph if the DB is new; callers check _word_to_ids bool.
        """
        return cls(name=path.stem, db_path=path)

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


# ── Cache path ─────────────────────────────────────────────────────────────────


def default_cache_path(name: str = "word_graph") -> Path:
    """
    G37: parameterised so recognition and generation graphs use separate files.
    Returns ~/.TheIgors/{name}.db (SQLite). Old .json files can be deleted.
    """
    return Path.home() / ".TheIgors" / f"{name}.db"
