"""Tests for devices/librarian/memory_writer.py."""

from __future__ import annotations

import pytest

from unseen_university.devices.librarian.memory_writer import (
    CodePayload,
    EmbeddingPayload,
    LinkPayload,
    PrimitivePayload,
    _extract_tags,
    write_memory,
)

# ── _extract_tags (fallback mode) ─────────────────────────────────────────────


def test_extract_tags_returns_list():
    tags = _extract_tags("Python error handling async await", force_fallback=True)
    assert isinstance(tags, list)
    assert len(tags) > 0


def test_extract_tags_fallback_nonempty_for_any_text():
    tags = _extract_tags("hello world foo bar baz", force_fallback=True)
    assert len(tags) >= 1


def test_extract_tags_empty_text_returns_general():
    tags = _extract_tags("", force_fallback=True)
    assert tags == ["general"]


# ── write_memory (no DB, force_fallback) ──────────────────────────────────────


def test_write_memory_requires_source_agent():
    with pytest.raises(ValueError, match="source_agent"):
        write_memory("some content", source_agent="")


def test_write_memory_returns_expected_keys():
    result = write_memory(
        "memory content about Python",
        source_agent="test-agent",
        force_fallback=True,
    )
    assert "id" in result
    assert "tags" in result
    assert "embedding_model" in result
    assert "source_agent" in result
    assert "stored_at" in result


def test_write_memory_source_agent_in_result():
    result = write_memory(
        "test content",
        source_agent="my-agent",
        force_fallback=True,
    )
    assert result["source_agent"] == "my-agent"


def test_write_memory_tags_are_list():
    result = write_memory(
        "Python async programming patterns",
        source_agent="agent-x",
        force_fallback=True,
    )
    assert isinstance(result["tags"], list)
    assert len(result["tags"]) > 0


def test_write_memory_extra_tags_merged():
    result = write_memory(
        "some content",
        source_agent="agent-x",
        extra_tags=["MyCustomTag"],
        force_fallback=True,
    )
    assert "MyCustomTag" in result["tags"]


def test_write_memory_embedding_model_recorded():
    result = write_memory(
        "embedding test content",
        source_agent="embed-agent",
        force_fallback=True,
    )
    assert result["embedding_model"] != ""


def test_write_memory_no_db_returns_no_db_id():
    result = write_memory(
        "offline content",
        source_agent="offline-agent",
        db_url="",
        force_fallback=True,
    )
    assert result["id"] == "no_db"


def test_write_memory_different_source_agents():
    r1 = write_memory("shared content", source_agent="agent-a", force_fallback=True)
    r2 = write_memory("shared content", source_agent="agent-b", force_fallback=True)
    assert r1["source_agent"] == "agent-a"
    assert r2["source_agent"] == "agent-b"


def test_write_memory_with_code_payload():
    from dataclasses import asdict

    payload = asdict(CodePayload(language="python", snippet="def foo(): pass"))
    result = write_memory(
        "function definition",
        source_agent="code-agent",
        payloads={"code": payload},
        db_url="",
        force_fallback=True,
    )
    assert result["id"] == "no_db"  # no DB in test env


def test_write_memory_with_source_token_accepted():
    result = write_memory(
        "provenance test",
        source_agent="rack-agent",
        source_token="tok_abc123",
        db_url="",
        force_fallback=True,
    )
    assert result["id"] == "no_db"


def test_write_memory_with_derived_from_accepted():
    result = write_memory(
        "derived memory content",
        source_agent="librarian-recall",
        derived_from=["mem-id-a", "mem-id-b"],
        db_url="",
        force_fallback=True,
    )
    assert result["id"] == "no_db"


def test_write_memory_stored_at_is_iso():
    result = write_memory("ts check", source_agent="ts-agent", force_fallback=True)
    from datetime import datetime

    # Should parse without error
    datetime.fromisoformat(result["stored_at"])


# ── Payload dataclasses ───────────────────────────────────────────────────────


def test_embedding_payload_fields():
    ep = EmbeddingPayload(
        vector=[0.1, 0.2], model="test", dimension=2, computed_at="2026-01-01"
    )
    assert ep.vector == [0.1, 0.2]
    assert ep.dimension == 2


def test_code_payload_fields():
    cp = CodePayload(language="python", snippet="x = 1", file_path="foo.py")
    assert cp.language == "python"
    assert cp.file_path == "foo.py"


def test_link_payload_fields():
    lp = LinkPayload(url="https://example.com", title="Example")
    assert lp.url == "https://example.com"


def test_primitive_payload_fields():
    pp = PrimitivePayload(value=42, type_hint="int")
    assert pp.value == 42
    assert pp.type_hint == "int"
