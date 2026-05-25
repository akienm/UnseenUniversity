"""
chunker — atomic input splitter for distributed preparse.

T-input-chunker (D-preparse-distribution-2026-04-22).

Pure function. Splits input text into atomic units — sentence-level by
default, with force-splits at discourse markers ("but", "however",
"also", "oh and", "by the way", "anyway") and a clause-level fallback
for sentences that exceed a token budget. Paragraph boundaries
(double-newline) are hard.

Each emitted chunk carries a context-carry field referencing the
previous 1-2 atoms, so downstream consumers can resolve pronouns and
"it"/"that" references without the splitter needing to know intent.

No routing awareness here. T-preparse-router consumes these chunks
with capacity-profile information to group atoms into machine-sized
batches.

Usage:
    from devices.igor.cognition.chunker import chunk_input

    for chunk in chunk_input(text):
        # chunk.text — the atom's text
        # chunk.context_carry — tuple of up to 2 prior atoms (strings)
        # chunk.kind — "sentence" | "clause" | "fragment"
        ...
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Approximate tokens-per-word for English; chars/4 is a looser proxy.
# Used only for the clause-fallback cap, not for classification.
_DEFAULT_MAX_TOKENS_PER_CHUNK = 150


# Discourse markers that justify force-splitting mid-sentence. Each marker
# appears at a clause boundary in ordinary speech ("X, but Y" -> two atoms).
# Order matters only for regex alternation: longer patterns first so "by the
# way" wins over "by" if we were to match partial, but anchored boundaries
# make this moot in practice.
_DISCOURSE_MARKERS: tuple[str, ...] = (
    "oh and",
    "oh, and",
    "by the way",
    "by the way,",
    "however",
    "anyway",
    "anyways",
    "also",
    "meanwhile",
    "otherwise",
    "furthermore",
    "moreover",
    "nonetheless",
    "nevertheless",
    "that said",
    "but also",
    "but",
)


_WORD_RE = re.compile(r"\b\w+\b")


@dataclass
class Chunk:
    """A single atomic unit of input.

    Fields
    ------
    text
        The atom's text, stripped of leading/trailing whitespace.
    kind
        "sentence" if the atom is a whole sentence, "clause" if it came
        from a clause-level fallback split, "fragment" if it's leftover
        non-sentence text (e.g. a single word).
    context_carry
        Tuple of up to 2 prior chunks' text (chronologically nearest
        first), for downstream pronoun/reference resolution.
    """

    text: str
    kind: str = "sentence"
    context_carry: tuple[str, ...] = field(default_factory=tuple)


def _approx_tokens(s: str) -> int:
    """Cheap token estimate — word count is close enough for the cap."""
    return len(_WORD_RE.findall(s))


def _split_sentences(text: str) -> list[str]:
    """Sentence-split on .!? with quote-awareness and abbrev tolerance.

    Not perfect — deliberately simple. Edge cases (Mr./Dr./e.g./etc.) fall
    into the next atom rather than splitting prematurely.
    """
    if not text:
        return []

    # Quote-aware: inside "..." or '...' we don't split on sentence marks.
    out: list[str] = []
    buf: list[str] = []
    in_dq = False
    in_sq = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        buf.append(ch)
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            in_dq = not in_dq
        elif ch == "'" and (i == 0 or text[i - 1] != "\\"):
            in_sq = not in_sq
        elif ch in ".!?" and not in_dq and not in_sq:
            # Guard common abbrevs: Mr., Dr., Mrs., e.g., i.e., etc.
            _tail = "".join(buf).rstrip()
            if _tail.endswith(("Mr.", "Mrs.", "Ms.", "Dr.", "e.g.", "i.e.", "etc.")):
                i += 1
                continue
            # Lookahead: consume the trailing space(s), then split.
            j = i + 1
            while j < n and text[j] in " \t":
                buf.append(text[j])
                j += 1
            # Only commit if next char is upper-case letter or end-of-text
            # or newline — avoids splitting on decimal points like "3.14".
            if j >= n or text[j] in "\r\n" or text[j].isupper() or text[j] == '"':
                out.append("".join(buf).strip())
                buf = []
                i = j
                continue
        i += 1
    if buf:
        leftover = "".join(buf).strip()
        if leftover:
            out.append(leftover)
    return out


def _force_split_discourse(sentence: str) -> list[str]:
    """Force-split a sentence at discourse markers. Returns one or more
    fragments; if no marker fires, returns [sentence] unchanged."""
    lowered = sentence.lower()
    # Find the earliest marker position (case-insensitive, word-bounded).
    earliest_idx: int | None = None
    earliest_marker: str | None = None
    for marker in _DISCOURSE_MARKERS:
        # Look for the marker as a standalone word(s) with a preceding
        # boundary (start-of-string, comma, or whitespace).
        pattern = r"(?:^|[,\s])(" + re.escape(marker) + r")\b"
        m = re.search(pattern, lowered)
        if m is None:
            continue
        idx = m.start(1)
        # Skip if at the very start of the sentence — nothing to split off.
        if idx == 0:
            continue
        if earliest_idx is None or idx < earliest_idx:
            earliest_idx = idx
            earliest_marker = marker
    if earliest_idx is None:
        return [sentence]

    # Split preserving the marker with the trailing fragment.
    left = sentence[:earliest_idx].rstrip(" ,;:")
    right = sentence[earliest_idx:].strip()
    # Recurse into the right-hand side in case there are more markers.
    tail_parts = _force_split_discourse(right)
    out = [left] if left else []
    out.extend(tail_parts)
    return [p for p in out if p]


def _clause_fallback(sentence: str, max_tokens: int) -> list[str]:
    """If a single sentence exceeds the token cap, split at commas /
    conjunctions. Last-resort: hard split by word count."""
    if _approx_tokens(sentence) <= max_tokens:
        return [sentence]

    # First pass: split on commas / semicolons / conjunctions.
    pieces = re.split(r"(?:,\s+|;\s+|\s+and\s+|\s+but\s+|\s+or\s+)", sentence)
    pieces = [p.strip() for p in pieces if p.strip()]
    if not pieces:
        return [sentence]
    # Second pass: any piece still over cap → word-split chunks of max_tokens.
    out: list[str] = []
    for p in pieces:
        if _approx_tokens(p) <= max_tokens:
            out.append(p)
            continue
        words = p.split()
        for k in range(0, len(words), max_tokens):
            out.append(" ".join(words[k : k + max_tokens]))
    return out


def chunk_input(
    text: str,
    *,
    max_tokens_per_chunk: int = _DEFAULT_MAX_TOKENS_PER_CHUNK,
    context_carry_depth: int = 2,
) -> list[Chunk]:
    """Split text into atomic chunks with context-carry populated.

    Pipeline:
      1. Split on paragraph boundaries (double newline) — hard.
      2. Per paragraph, sentence-split on .!? (quote/abbrev-aware).
      3. Per sentence, force-split on discourse markers.
      4. Per resulting unit, if it exceeds max_tokens_per_chunk, fall back
         to clause-level / hard word-count split.
      5. Populate context_carry on every chunk with up to
         `context_carry_depth` prior chunk texts.

    Pure function; no side effects, no global state.
    """
    if text is None:
        return []
    text = text.strip()
    if not text:
        return []

    # Paragraph split — hard boundary on 2+ newlines.
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]

    raw_units: list[tuple[str, str]] = []  # (text, kind)
    for para in paragraphs:
        for sent in _split_sentences(para):
            if not sent:
                continue
            for piece in _force_split_discourse(sent):
                if not piece:
                    continue
                for sub in _clause_fallback(piece, max_tokens_per_chunk):
                    if not sub:
                        continue
                    # kind classification (order matters):
                    #   < 3 tokens → "fragment" (greeting/ack-shaped)
                    #   == full sentence and no clause-split → "sentence"
                    #   otherwise → "clause"
                    if _approx_tokens(sub) < 3:
                        raw_units.append((sub, "fragment"))
                    elif sub == sent:
                        raw_units.append((sub, "sentence"))
                    else:
                        raw_units.append((sub, "clause"))

    chunks: list[Chunk] = []
    for i, (txt, kind) in enumerate(raw_units):
        carry_start = max(0, i - context_carry_depth)
        carry = tuple(t for t, _ in raw_units[carry_start:i])
        chunks.append(Chunk(text=txt, kind=kind, context_carry=carry))
    return chunks
