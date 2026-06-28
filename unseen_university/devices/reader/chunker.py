"""
chunker.py — shared text chunker for ReaderDevice output modes.

Extracted from devices/summarizer/device.py._chunk_text() so both
summary mode and node mode (and future output modes) share one implementation.
SummarizerDevice retains its own copy until it is retired.

Splits on paragraph boundaries first; falls back to word-count splitting
when a single paragraph exceeds max_words.
"""

from __future__ import annotations

import os
import re

_CHUNK_MAX_WORDS = int(os.environ.get("READER_CHUNK_WORDS", "500"))


def chunk_text(text: str, max_words: int = _CHUNK_MAX_WORDS) -> list[str]:
    """Split text into chunks of up to max_words words on paragraph boundaries.

    Returns a list of at least one chunk. Empty/whitespace-only input returns
    a single empty-string chunk so callers always get a list.
    """
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    if not paragraphs:
        return [text] if text.strip() else [""]

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0

    for para in paragraphs:
        words = para.split()
        para_words = len(words)

        if para_words > max_words:
            if current:
                chunks.append("\n\n".join(current))
                current = []
                current_words = 0
            for i in range(0, para_words, max_words):
                chunks.append(" ".join(words[i : i + max_words]))
            continue

        if current and current_words + para_words > max_words:
            chunks.append("\n\n".join(current))
            current = [para]
            current_words = para_words
        else:
            current.append(para)
            current_words += para_words

    if current:
        chunks.append("\n\n".join(current))

    return chunks or [text]
