"""
WordGraph — lightweight in-memory word co-occurrence index.

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

At human conversational speed (~2-3 words/second, <100 habits) this is a
handful of dict lookups per turn — no database, no network, no API call.

Boot:  built from habit triggers + narratives; optionally loaded from JSON cache.
Learn: reinforce() boosts a document's word weights when it activates.
Save:  persisted to ~/.TheIgors/word_graph.json after each reinforcement.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from pathlib import Path

# ── Stopwords ─────────────────────────────────────────────────────────────────
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "ought",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "as", "into", "through", "during", "before", "after", "above", "below",
    "up", "down", "out", "off", "over", "under", "again", "then", "once",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "it", "they",
    "what", "which", "who", "this", "that", "these", "those",
    "and", "or", "but", "if", "while", "so", "because", "when", "where", "how",
    "not", "no", "nor", "just", "very", "also", "more", "most", "any", "all",
    # French common stopwords (intentionally small — let content words through)
    "le", "la", "les", "un", "une", "des", "du", "de", "et", "en", "est",
    "il", "elle", "ils", "elles", "je", "tu", "nous", "vous", "on",
    "que", "qui", "dans", "sur", "par", "avec", "pour", "au", "aux",
    # Dutch
    "de", "het", "een", "van", "in", "is", "dat", "op", "te", "zijn",
    "er", "maar", "om", "dit", "die", "ook", "bij", "als", "dan", "nog",
})


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


# ── WordGraph ─────────────────────────────────────────────────────────────────

class WordGraph:
    """
    In-memory word graph with language tags on nodes (#141).

    _word_to_ids : word → {doc_id: weight}   — parsing direction
    _cooccur     : word → {word: count}       — generation direction (crosses lang)
    _word_lang   : word → lang_tag            — language of each node
    _idf         : word → float               — built after indexing

    G37: name param allows two instances — recognition (listening) and generation
    (speaking) — with separate cache paths and independent weight development.
    """

    def __init__(self, name: str = "word_graph") -> None:
        self.name = name                           # G37: "word_graph" | "generation_graph"
        self._word_to_ids: dict[str, dict[str, float]] = defaultdict(dict)
        self._cooccur: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        self._word_lang: dict[str, str] = {}      # #141: word → lang tag
        self._idf: dict[str, float] = {}
        self._doc_count: int = 0

    # ── Indexing ───────────────────────────────────────────────────────────────

    def index(self, doc_id: str, text: str, weight: float = 1.0,
              lang: str = "en") -> None:
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
        self._doc_count += 1
        unique = list(dict.fromkeys(tokens))   # preserve order, dedupe
        for w in unique:
            self._word_to_ids[w][doc_id] = max(
                self._word_to_ids[w].get(doc_id, 0.0), weight
            )
            # Tag word with lang only if not already tagged (first writer wins)
            if w not in self._word_lang:
                self._word_lang[w] = lang
            for w2 in unique:
                if w2 != w:
                    self._cooccur[w][w2] += 1.0

    def build_idf(self) -> None:
        """Compute IDF weights. Call once after all index() calls."""
        n = max(self._doc_count, 1)
        self._idf = {
            w: math.log(n / max(len(ids), 1))
            for w, ids in self._word_to_ids.items()
        }

    # ── Parsing direction ──────────────────────────────────────────────────────

    def score(self, input_text: str, doc_ids: list[str],
              lang: str | None = None) -> dict[str, float]:
        """
        Score each doc_id by TF-IDF word overlap with input_text.
        Returns {doc_id: score} normalised to [0, 1].

        lang: if specified, only words tagged with that language contribute.
              None (default) uses all words — cross-language scoring.
        """
        words = set(tokenize_with_bigrams(input_text))
        if not words or not doc_ids:
            return {}

        if lang is not None:
            words = {w for w in words if self._word_lang.get(w, "en") == lang}
        if not words:
            return {}

        raw: dict[str, float] = {}
        for doc_id in doc_ids:
            total = 0.0
            for w in words:
                if doc_id in self._word_to_ids.get(w, {}):
                    total += self._word_to_ids[w][doc_id] * self._idf.get(w, 1.0)
            if total > 0:
                raw[doc_id] = total

        if not raw:
            return {}
        max_score = max(raw.values())
        return {k: v / max_score for k, v in raw.items()}

    # ── Generation direction ───────────────────────────────────────────────────

    def predict_next(self, context_text: str, n: int = 5,
                     lang: str | None = None,
                     milieu_state: dict | None = None) -> list[tuple[str, float]]:
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
        words = tokenize_with_bigrams(context_text)
        counts: dict[str, float] = defaultdict(float)
        for w in words:
            for co_word, weight in self._cooccur.get(w, {}).items():
                if lang is None or self._word_lang.get(co_word, "en") == lang:
                    counts[co_word] += weight
        if not counts:
            return []
        # G37: milieu tilt — arousal sharpens gradient (temperature-like)
        if milieu_state is not None:
            arousal = float(milieu_state.get("arousal", 0.5))
            # map arousal [0,1] → exponent [0.5, 2.0]: high arousal = steep gradient
            exponent = 0.5 + arousal * 1.5
            counts = {w: v ** exponent for w, v in counts.items()}
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

    # ── Graph analysis ─────────────────────────────────────────────────────────

    def top_hubs(self, n: int = 10, words_only: bool = True,
                 lang: str | None = None) -> list[tuple[str, int]]:
        """
        Return the N most-connected words by co-occurrence neighbour count.
        words_only=True skips bigram tokens (a__b) to keep results readable.
        lang: optional filter to a specific language.
        """
        items = (
            (w, len(co))
            for w, co in self._cooccur.items()
            if not (words_only and "__" in w)
            and (lang is None or self._word_lang.get(w, "en") == lang)
        )
        return sorted(items, key=lambda x: x[1], reverse=True)[:n]

    def bridge_words(self, word_a: str, word_b: str,
                     n: int = 10) -> list[tuple[str, float]]:
        """
        Find words that co-occur with BOTH word_a and word_b — the connective
        tissue between two concepts. Ranked by combined co-occurrence weight.
        Works across language boundaries (cross-language bridges are valid).
        Returns [] if either word is not in the graph.
        """
        co_a = self._cooccur.get(word_a.lower(), {})
        co_b = self._cooccur.get(word_b.lower(), {})
        shared = set(co_a) & set(co_b)
        if not shared:
            return []
        ranked = sorted(
            ((w, co_a[w] + co_b[w]) for w in shared if "__" not in w),
            key=lambda x: x[1],
            reverse=True,
        )
        return ranked[:n]

    def domain_exclusive(self, doc_prefix: str, n: int = 10) -> list[str]:
        """
        Find words that appear ONLY in docs whose id starts with doc_prefix.
        Useful for isolating specialised vocabulary (e.g. 'hamlet_' or 'neuro_').
        """
        exclusive = []
        for w, doc_weights in self._word_to_ids.items():
            if "__" in w:
                continue
            if doc_weights and all(doc_id.startswith(doc_prefix) for doc_id in doc_weights):
                exclusive.append(w)
        exclusive.sort(
            key=lambda w: sum(self._word_to_ids[w].values()),
            reverse=True,
        )
        return exclusive[:n]

    def words_by_lang(self, lang: str) -> list[str]:
        """
        Return all word nodes tagged with the given language.
        Bigram tokens (w1__w2) are excluded — unigrams only.
        Useful for inspecting language-specific vocabulary or navigating
        deliberately between languages.
        """
        return [
            w for w, l in self._word_lang.items()
            if l == lang and "__" not in w
        ]

    # ── Learning ───────────────────────────────────────────────────────────────

    def reinforce(self, doc_id: str, boost: float = 0.1) -> None:
        """
        Boost word weights for a document that just activated (e.g. habit fired).
        Experiences gradually reshape word weights — the learning loop.
        Capped at 2.0 to prevent runaway dominance.
        """
        for ids in self._word_to_ids.values():
            if doc_id in ids:
                ids[doc_id] = min(ids[doc_id] + boost, 2.0)

    def reinforce_text(self, text: str, boost: float = 0.05,
                       lang: str = "en") -> None:
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
        for w in unique:
            for w2 in unique:
                if w2 != w:
                    current = self._cooccur[w].get(w2, 0.0)
                    self._cooccur[w][w2] = min(current + boost, 2.0)

    # ── Persistence ───────────────────────────────────────────────────────────

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({
                "doc_count": self._doc_count,
                "word_to_ids": {w: dict(ids) for w, ids in self._word_to_ids.items()},
                "cooccur": {w: dict(co) for w, co in self._cooccur.items()},
                "word_lang": dict(self._word_lang),   # #141
            }), encoding="utf-8")
        except Exception:
            pass

    @classmethod
    def load(cls, path: Path) -> "WordGraph":
        """Load from JSON cache, or return an empty graph if missing/corrupt."""
        g = cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            g._doc_count = data.get("doc_count", 0)
            for w, ids in data.get("word_to_ids", {}).items():
                g._word_to_ids[w] = ids
            for w, co in data.get("cooccur", {}).items():
                g._cooccur[w] = defaultdict(float, co)
            # #141: load lang tags; default to "en" for pre-existing graphs
            _saved_langs = data.get("word_lang", {})
            for w in g._word_to_ids:
                g._word_lang[w] = _saved_langs.get(w, "en")
            g.build_idf()
        except Exception:
            pass
        return g

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
            lang = (h.metadata or {}).get("lang", "en")   # #141
            if trigger:
                g.index(h.id, trigger, weight=2.0, lang=lang)
            if h.narrative:
                g.index(h.id, h.narrative, weight=1.0, lang=lang)
        g.build_idf()
        return g


# ── Cache path ────────────────────────────────────────────────────────────────

def default_cache_path(name: str = "word_graph") -> Path:
    """G37: parameterised so recognition and generation graphs use separate caches."""
    return Path.home() / ".TheIgors" / f"{name}.json"
