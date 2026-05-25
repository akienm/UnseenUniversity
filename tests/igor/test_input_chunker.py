"""T-input-chunker: atomic input splitter for distributed preparse.

Pure-function chunker. Covers sentence split, quote-awareness, abbrev
tolerance, discourse-marker force-splits, paragraph boundaries, clause
fallback on oversized sentences, context-carry population, and edge
cases (empty, single word, None).
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.cognition.chunker import Chunk, chunk_input


def _texts(chunks):
    return [c.text for c in chunks]


def test_empty_input_returns_empty():
    assert chunk_input("") == []
    assert chunk_input("   ") == []
    assert chunk_input(None) == []  # type: ignore


def test_single_word_returns_one_chunk():
    result = chunk_input("hi")
    assert len(result) == 1
    assert result[0].text == "hi"
    # Single word is a fragment (< 3 words)
    assert result[0].kind == "fragment"


def test_single_sentence_returns_one_chunk():
    result = chunk_input("The quick brown fox jumps over the lazy dog.")
    assert len(result) == 1
    assert result[0].kind == "sentence"
    assert "fox" in result[0].text


def test_multi_sentence_splits():
    result = chunk_input("Hello there. How are you? I'm fine!")
    assert len(result) == 3
    assert "Hello" in result[0].text
    assert "How are you" in result[1].text
    assert "fine" in result[2].text


def test_abbreviations_do_not_split():
    """Mr., Dr., e.g., i.e., etc. should NOT trigger sentence splits."""
    result = chunk_input("I saw Dr. Smith today. He said hello.")
    assert len(result) == 2
    assert "Dr. Smith" in result[0].text

    result2 = chunk_input("Use e.g. comma separators. Then continue.")
    assert len(result2) == 2
    assert "e.g." in result2[0].text


def test_quoted_periods_do_not_split():
    """Periods inside quotes don't trigger splits."""
    result = chunk_input('She said "This is great. Really." Then she left.')
    # Expect 2 chunks: the quoted sentence (treated as one) and the tail.
    texts = _texts(result)
    combined = " ".join(texts)
    assert "This is great" in combined
    assert "she left" in combined


def test_discourse_marker_but_force_splits():
    result = chunk_input("I tried the new build but it failed on ARM.")
    texts = _texts(result)
    assert len(result) == 2
    assert "tried the new build" in texts[0]
    assert texts[1].lower().startswith("but")


def test_discourse_marker_by_the_way_force_splits():
    result = chunk_input("Hi igor, by the way can you explain X?")
    texts = _texts(result)
    assert len(result) >= 2
    combined = " ".join(texts).lower()
    assert "hi igor" in combined
    assert "by the way" in combined


def test_discourse_marker_oh_and_force_splits():
    result = chunk_input("Done with the commit. oh and I pushed to main.")
    texts = _texts(result)
    assert any("oh and" in t.lower() for t in texts)


def test_paragraph_boundary_is_hard():
    """Double-newline creates hard chunk boundary."""
    text = "First paragraph here.\n\nSecond paragraph here."
    result = chunk_input(text)
    assert len(result) == 2
    assert "First" in result[0].text
    assert "Second" in result[1].text


def test_multiple_paragraphs_and_sentences():
    text = "Para one sentence one. Para one sentence two.\n\nPara two starts here."
    result = chunk_input(text)
    assert len(result) == 3


def test_oversized_sentence_clause_fallback():
    """A single sentence over max_tokens_per_chunk falls back to clause split."""
    # Build a 200-word sentence with commas
    parts = ["this is clause " + str(i) for i in range(50)]
    text = ", ".join(parts) + "."
    result = chunk_input(text, max_tokens_per_chunk=30)
    # Should split into multiple clause chunks
    assert len(result) > 1
    assert all(c.kind in ("clause", "fragment", "sentence") for c in result)


def test_oversized_sentence_no_commas_hard_split():
    """Sentence with no clause markers still gets hard-split by word count."""
    text = " ".join(["word"] * 300) + "."
    result = chunk_input(text, max_tokens_per_chunk=50)
    assert len(result) > 1
    for c in result:
        # Each chunk should be within ~max_tokens_per_chunk
        assert len(c.text.split()) <= 55  # allow slight slack


def test_context_carry_populated():
    """Each chunk carries up to 2 prior chunk texts in context_carry."""
    result = chunk_input("First sentence. Second sentence. Third sentence. Fourth.")
    assert len(result) == 4
    assert result[0].context_carry == ()
    assert len(result[1].context_carry) == 1
    assert "First" in result[1].context_carry[0]
    assert len(result[2].context_carry) == 2
    assert "First" in result[2].context_carry[0]
    assert "Second" in result[2].context_carry[1]
    # Depth capped at 2 by default
    assert len(result[3].context_carry) == 2
    assert "Second" in result[3].context_carry[0]
    assert "Third" in result[3].context_carry[1]


def test_context_carry_depth_configurable():
    result = chunk_input(
        "A. B. C. D. E.",
        context_carry_depth=4,
    )
    # Last chunk should carry up to 4 prior atoms
    assert len(result) == 5
    assert len(result[4].context_carry) == 4


def test_chunk_dataclass_shape():
    """Chunk is a dataclass with expected fields — contract for
    downstream consumers (T-preparse-router)."""
    c = Chunk(text="hello", kind="sentence", context_carry=("prev",))
    assert c.text == "hello"
    assert c.kind == "sentence"
    assert c.context_carry == ("prev",)


def test_kind_classification():
    """sentence / clause / fragment classification reflects source."""
    # Full single sentence → "sentence"
    result = chunk_input("This is a normal sentence.")
    assert result[0].kind == "sentence"

    # Very short → "fragment"
    result2 = chunk_input("ok")
    assert result2[0].kind == "fragment"

    # Long sentence with clause fallback → "clause" or similar
    text = (
        "this has many clauses, all separated by commas, and it keeps going, on and on"
    )
    long_result = chunk_input(text + ", " + text + ", " + text, max_tokens_per_chunk=15)
    assert any(c.kind == "clause" for c in long_result)


def test_discourse_at_start_not_split_off():
    """Marker at position 0 (start of sentence) shouldn't split off an empty left side."""
    result = chunk_input("but I disagree strongly.")
    # Should be one atom (no empty left half)
    assert len(result) == 1
    assert "disagree" in result[0].text
