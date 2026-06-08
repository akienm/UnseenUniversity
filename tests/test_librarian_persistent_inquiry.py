"""Tests for devices/librarian/persistent_inquiry.py."""

from __future__ import annotations

import os

import psycopg2
import pytest

from devices.librarian.persistent_inquiry import (
    WEIGHT_TYPE_VALUES,
    _compress_hits,
    _topic_slug,
    _weight_for_type,
    add_hit,
    become_knowledgeable_about,
    compress,
    get_inquiry,
)

# ── Pure unit tests (no DB) ────────────────────────────────────────────────────


def test_topic_slug_lowercases_and_dashes():
    assert _topic_slug("Python Error Handling") == "python-error-handling"


def test_topic_slug_replaces_spaces():
    assert _topic_slug("how memory works") == "how-memory-works"


def test_topic_slug_strips_special_chars():
    slug = _topic_slug("C++ templates & generics")
    assert slug == "c-templates-generics"


def test_topic_slug_empty_returns_general():
    assert _topic_slug("") == "general"
    assert _topic_slug("!!!") == "general"


def test_weight_confirmation_is_lowest():
    assert _weight_for_type("confirmation") < _weight_for_type("gap_explanation")


def test_weight_serendipitous_is_highest():
    assert _weight_for_type("serendipitous") > _weight_for_type("gap_explanation")
    assert _weight_for_type("serendipitous") == WEIGHT_TYPE_VALUES["serendipitous"]


def test_weight_unknown_defaults_to_confirmation():
    assert _weight_for_type("unknown_type") == WEIGHT_TYPE_VALUES["confirmation"]


def test_compress_hits_sorts_by_weight_descending():
    hits = [
        {"text": "low", "weight": 0.3},
        {"text": "high", "weight": 1.0},
        {"text": "mid", "weight": 0.6},
    ]
    result = _compress_hits(hits, max_keep=2)
    assert result.startswith("high")
    assert "mid" in result
    assert "low" not in result


def test_compress_hits_respects_max_keep():
    hits = [{"text": f"hit{i}", "weight": float(i)} for i in range(10)]
    result = _compress_hits(hits, max_keep=3)
    # 3 items joined by " | " → 2 separators
    assert result.count(" | ") == 2


def test_compress_hits_produces_shorter_than_full_concatenation():
    hits = [{"text": f"long detailed text fragment number {i}", "weight": float(i)} for i in range(10)]
    full = " ".join(h["text"] for h in hits)
    compressed = _compress_hits(hits, max_keep=3)
    assert len(compressed) < len(full)


def test_compress_empty_hits_returns_empty_string():
    assert _compress_hits([], max_keep=5) == ""


def test_compress_hits_with_max_keep_larger_than_count():
    hits = [{"text": "only one", "weight": 1.0}]
    result = _compress_hits(hits, max_keep=10)
    assert result == "only one"


# ── Integration tests (real palace DB) ────────────────────────────────────────

_DB_URL = os.environ.get("IGOR_HOME_DB_URL", "")
_SENTINEL = "cc-test-persistent-inquiry-xray-sentinel"


@pytest.fixture
def test_topic():
    """Unique topic per test; cleans up palace rows on teardown."""
    yield _SENTINEL
    if _DB_URL:
        try:
            conn = psycopg2.connect(_DB_URL)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "DELETE FROM adc.palace WHERE path LIKE 'palace.library.inquiry.cc-test%'"
                    )
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass


@pytest.mark.skipif(not _DB_URL, reason="IGOR_HOME_DB_URL not set")
def test_become_knowledgeable_about_creates_inquiry(test_topic):
    result = become_knowledgeable_about(test_topic, db_url=_DB_URL)
    assert result["topic"] == test_topic
    assert result["current_model"] == ""
    assert result["path"].startswith("palace.library.inquiry.")


@pytest.mark.skipif(not _DB_URL, reason="IGOR_HOME_DB_URL not set")
def test_become_knowledgeable_about_is_idempotent(test_topic):
    r1 = become_knowledgeable_about(test_topic, db_url=_DB_URL)
    r2 = become_knowledgeable_about(test_topic, db_url=_DB_URL)
    assert r1["path"] == r2["path"]


@pytest.mark.skipif(not _DB_URL, reason="IGOR_HOME_DB_URL not set")
def test_add_hit_accumulates_with_differential_weights(test_topic):
    become_knowledgeable_about(test_topic, db_url=_DB_URL)
    add_hit(test_topic, "confirms existing idea", weight_type="confirmation", db_url=_DB_URL)
    add_hit(test_topic, "explains a gap in understanding", weight_type="gap_explanation", db_url=_DB_URL)
    inq = get_inquiry(test_topic, db_url=_DB_URL)
    assert inq["hit_count"] == 2
    hits_by_type = {h["weight_type"]: h["weight"] for h in inq["hits"]}
    assert hits_by_type["confirmation"] < hits_by_type["gap_explanation"]


@pytest.mark.skipif(not _DB_URL, reason="IGOR_HOME_DB_URL not set")
def test_compress_produces_denser_model_than_full_hit_list(test_topic):
    become_knowledgeable_about(test_topic, db_url=_DB_URL)
    for i in range(6):
        add_hit(test_topic, f"detailed hit text fragment number {i} with many words", weight_type="confirmation", db_url=_DB_URL)
    add_hit(test_topic, "serendipitous surprise discovery connection", weight_type="serendipitous", db_url=_DB_URL)

    model = compress(test_topic, max_hits_to_keep=3, db_url=_DB_URL)
    inq = get_inquiry(test_topic, db_url=_DB_URL)
    all_text = " ".join(h["text"] for h in inq["hits"])

    assert len(model) < len(all_text)
    assert inq["last_compressed_at"] is not None
    assert inq["current_model"] == model


@pytest.mark.skipif(not _DB_URL, reason="IGOR_HOME_DB_URL not set")
def test_get_inquiry_returns_none_for_unknown_topic():
    result = get_inquiry("this-topic-absolutely-does-not-exist-xray-99", db_url=_DB_URL)
    assert result is None


@pytest.mark.skipif(not _DB_URL, reason="IGOR_HOME_DB_URL not set")
def test_inquiry_survives_fresh_connection(test_topic):
    become_knowledgeable_about(test_topic, db_url=_DB_URL)
    add_hit(test_topic, "a hit that must persist across connections", weight_type="serendipitous", db_url=_DB_URL)
    # New call = new connection, simulates restart
    inq = get_inquiry(test_topic, db_url=_DB_URL)
    assert inq is not None
    assert inq["hit_count"] >= 1
    assert any(h["text"] == "a hit that must persist across connections" for h in inq["hits"])


@pytest.mark.skipif(not _DB_URL, reason="IGOR_HOME_DB_URL not set")
def test_add_hit_raises_for_nonexistent_topic():
    with pytest.raises(ValueError, match="become_knowledgeable_about"):
        add_hit("topic-that-was-never-created-xray", "text", db_url=_DB_URL)
