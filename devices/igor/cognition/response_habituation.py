"""
Response word habituation — WO#140 Phase 2.

Passively tracks frequency of words Igor produces in outgoing responses.
High-frequency words decay faster (more habituated = less novel signal).
Rare words stay sharp longer.

Storage: ~/.TheIgors/<instance>/response_habituation.json (see paths().instance_dir)
Gate: IGOR_RESPONSE_HABITUATION (default true)

Design note: kept separate from the word_graph to avoid bloat — the word graph
tracks parsing/generation weights; this tracks output vocabulary novelty.
"""

from __future__ import annotations
import logging

import json
import math
import re
import time
from pathlib import Path
from ..igor_base import get_logger
from ..igor_base import IgorBase

_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "i",
        "to",
        "of",
        "and",
        "or",
        "in",
        "it",
        "that",
        "this",
        "for",
        "on",
        "with",
        "be",
        "have",
        "do",
        "at",
        "by",
        "from",
        "as",
        "but",
        "not",
        "you",
        "we",
        "they",
        "he",
        "she",
    }
)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z']{2,}", text.lower())


class ResponseHabituation(IgorBase):
    """
    Frequency + last_seen tracker for Igor's outgoing vocabulary.

    decay_factor(word) → [0,1]:
      1.0 = never seen before (fully fresh)
      <1.0 = habituated; decreases with count and recency
    """

    TAU_BASE_SECS = 7 * 86_400  # 7-day base half-life for new words
    TAU_SCALE_MAX = 4.0  # max 4× for very frequent words (28-day ceiling)

    def __init__(self, path: Path) -> None:
        self._path = path
        # {word: {"count": int, "last_seen": float (unix timestamp)}}
        self._store: dict[str, dict] = {}
        self._load()

    # ── persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._path.exists():
            try:
                with self._path.open() as f:
                    self._store = json.load(f)
            except Exception:
                self._store = {}

    def save(self) -> None:
        try:
            with self._path.open("w") as f:
                json.dump(self._store, f)
        except Exception as _bare_e:
            get_logger(__name__).warning(
                "bare except in wild_igor/igor/cognition/response_habituation.py: %s",
                _bare_e,
            )

    # ── core operations ────────────────────────────────────────────────────────

    def observe(self, response_text: str) -> None:
        """Call with Igor's finalized reply text to update frequency counts."""
        now = time.time()
        for word in _tokenize(response_text):
            if word in _STOP_WORDS:
                continue
            entry = self._store.setdefault(word, {"count": 0, "last_seen": now})
            entry["count"] += 1
            entry["last_seen"] = now

    def decay_factor(self, word: str, now: float | None = None) -> float:
        """
        Habituation decay multiplier in [0, 1].
        High count → faster decay (more habituated → less novel).
        Unknown word → 1.0 (fully fresh).
        """
        entry = self._store.get(word)
        if entry is None:
            return 1.0
        now = now or time.time()
        count = entry["count"]
        age_secs = now - entry["last_seen"]
        tau_scale = min(1.0 + count * 0.3, self.TAU_SCALE_MAX)
        tau = self.TAU_BASE_SECS * tau_scale
        return math.exp(-age_secs / tau)

    def top_habituated(self, n: int = 20) -> list[tuple[str, int, float]]:
        """Top-N most habituated words: (word, count, decay_factor)."""
        now = time.time()
        ranked = [
            (w, e["count"], self.decay_factor(w, now)) for w, e in self._store.items()
        ]
        ranked.sort(key=lambda x: x[1], reverse=True)
        return ranked[:n]

    def vocab_size(self) -> int:
        return len(self._store)
