"""T-memory-tag-metadata — tag:<name> convention via metadata JSONB.

Covers:
- pure helpers (extract_tag_names, build_tag_tree)
- cortex DB-backed methods (apply_tag, get_tags_for, memories_with_tag, tag_tree)
"""

from __future__ import annotations

import os
import uuid

import pytest

from devices.igor.memory.tag_tree import (
    TAG_PREFIX,
    build_tag_tree,
    extract_tag_names,
)

# ── pure helpers ──────────────────────────────────────────────────────────────


class TestExtractTagNames:
    def test_extracts_tagged_keys(self):
        meta = {"tag:foo": True, "tag:bar": "value", "other": 1}
        assert extract_tag_names(meta) == ["bar", "foo"]

    def test_empty_dict(self):
        assert extract_tag_names({}) == []

    def test_non_dict_returns_empty(self):
        assert extract_tag_names(None) == []
        assert extract_tag_names("not a dict") == []

    def test_ignores_empty_tag_name(self):
        assert extract_tag_names({"tag:": True}) == []

    def test_strips_prefix(self):
        assert extract_tag_names({"tag:pe_chain/situate": 1}) == ["pe_chain/situate"]


class TestBuildTagTree:
    def test_flat_tags(self):
        tree = build_tag_tree([["foo", "bar"], ["foo"]])
        assert tree["foo"]["_count"] == 2
        assert tree["bar"]["_count"] == 1

    def test_nested_tags(self):
        tree = build_tag_tree([["a/b/c"]])
        assert tree["a"]["_count"] == 1
        assert tree["a"]["b"]["_count"] == 1
        assert tree["a"]["b"]["c"]["_count"] == 1

    def test_count_aggregates_up(self):
        tree = build_tag_tree([["a"], ["a/b"], ["a/b/c"]])
        # 'a' appears directly once + shows up as prefix twice more = 3
        assert tree["a"]["_count"] == 3
        assert tree["a"]["b"]["_count"] == 2
        assert tree["a"]["b"]["c"]["_count"] == 1

    def test_empty_input(self):
        assert build_tag_tree([]) == {}

    def test_ignores_empty_parts(self):
        tree = build_tag_tree([["a//b"]])
        assert tree == {"a": {"_count": 1, "b": {"_count": 1}}}


# ── cortex DB-backed methods ──────────────────────────────────────────────────


def _cortex():
    """Build a cortex against the configured test DB. Skip if no URL."""
    if not os.getenv("IGOR_HOME_DB_URL"):
        pytest.skip("IGOR_HOME_DB_URL not set; cortex DB tests require Postgres")
    from pathlib import Path

    from devices.igor.memory.cortex import Cortex

    return Cortex(instance_id="tag-test")


def _store_factual(cortex, narrative: str, metadata: dict | None = None):
    from devices.igor.memory.models import Memory, MemoryType

    mid = f"T-mem-{uuid.uuid4().hex[:8]}"
    m = Memory(
        id=mid,
        narrative=narrative,
        memory_type=MemoryType.FACTUAL,
        metadata=metadata or {},
    )
    cortex.store(m)
    return mid


def test_apply_and_get_tags(tmp_path):
    cx = _cortex()
    mid = _store_factual(cx, "tag apply test")
    assert cx.apply_tag(mid, "workflow") is True
    assert "workflow" in cx.get_tags_for(mid)


def test_apply_tag_with_prefix_is_canonicalized(tmp_path):
    cx = _cortex()
    mid = _store_factual(cx, "canonicalize prefix")
    cx.apply_tag(mid, f"{TAG_PREFIX}debug")
    tags = cx.get_tags_for(mid)
    # Stored under 'tag:debug', returned as 'debug' (prefix stripped)
    assert "debug" in tags
    # Not double-prefixed
    assert "tag:debug" not in tags


def test_apply_tag_empty_rejected(tmp_path):
    cx = _cortex()
    mid = _store_factual(cx, "empty tag rejected")
    assert cx.apply_tag(mid, "") is False
    assert cx.apply_tag(mid, TAG_PREFIX) is False


def test_apply_tag_missing_memory(tmp_path):
    cx = _cortex()
    assert cx.apply_tag("T-does-not-exist-xyz", "foo") is False


def test_memories_with_tag_returns_matches(tmp_path):
    cx = _cortex()
    unique = f"unique-{uuid.uuid4().hex[:6]}"
    mid1 = _store_factual(cx, "memory with tag 1")
    mid2 = _store_factual(cx, "memory with tag 2")
    mid3 = _store_factual(cx, "memory without tag")
    cx.apply_tag(mid1, unique)
    cx.apply_tag(mid2, unique)
    ids = cx.memories_with_tag(unique)
    assert mid1 in ids
    assert mid2 in ids
    assert mid3 not in ids


def test_memories_with_tag_prefix_ignored(tmp_path):
    cx = _cortex()
    unique = f"prefix-{uuid.uuid4().hex[:6]}"
    mid = _store_factual(cx, "prefix lookup")
    cx.apply_tag(mid, unique)
    # Passing 'tag:<name>' is canonicalized identically
    assert mid in cx.memories_with_tag(f"{TAG_PREFIX}{unique}")


def test_tag_tree_includes_hierarchy(tmp_path):
    cx = _cortex()
    stamp = uuid.uuid4().hex[:6]
    mid1 = _store_factual(cx, "hierarchy root")
    mid2 = _store_factual(cx, "hierarchy leaf")
    cx.apply_tag(mid1, f"{stamp}-x")
    cx.apply_tag(mid2, f"{stamp}-x/y")
    tree = cx.tag_tree()
    root = tree.get(f"{stamp}-x")
    assert root is not None
    assert root["_count"] >= 2
    assert root["y"]["_count"] >= 1


# ── T-unified-memory-node-pilot: top-level tags + triggers columns ─────────────


def _query_memory_cols(cx, mid: str, *cols: str) -> dict:
    """Query specific columns from memories for a given id using Cortex's connection."""
    col_list = ", ".join(cols)
    with cx._conn() as conn:
        rows = conn.execute(f"SELECT {col_list} FROM memories WHERE id = %s", (mid,)).fetchall()
    if not rows:
        return {}
    row = rows[0]
    return dict(zip(cols, row)) if not hasattr(row, "keys") else dict(row)


def test_store_populates_tags_column():
    """Memory stored with metadata.tags → top-level tags column populated."""
    cx = _cortex()
    mid = _store_factual(cx, "tags column test", metadata={"tags": ["content", "test"]})
    row = _query_memory_cols(cx, mid, "tags")
    assert "content" in row["tags"], f"expected 'content' in tags column, got: {row['tags']}"
    assert "test" in row["tags"]


def test_store_empty_metadata_gives_empty_tags_column():
    """Memory with no metadata.tags gets tags=[] in the column."""
    cx = _cortex()
    mid = _store_factual(cx, "no tags memory")
    row = _query_memory_cols(cx, mid, "tags")
    assert row["tags"] == [] or row["tags"] is None


def test_reading_source_gets_auto_index_trigger():
    """Memory with source='reading' gets triggers.auto_index=true automatically."""
    from devices.igor.memory.models import Memory, MemoryType
    cx = _cortex()
    mid = f"T-trigger-{uuid.uuid4().hex[:8]}"
    m = Memory(
        id=mid,
        narrative="auto index trigger test",
        memory_type=MemoryType.FACTUAL,
        source="reading",
    )
    cx.store(m)
    row = _query_memory_cols(cx, mid, "triggers")
    assert row["triggers"].get("auto_index") is True, (
        f"reading source must get auto_index trigger, got: {row['triggers']}"
    )


def test_non_reading_source_no_auto_index_trigger():
    """Memory with source='interaction' does NOT get auto_index trigger."""
    from devices.igor.memory.models import Memory, MemoryType
    cx = _cortex()
    mid = f"T-notrigger-{uuid.uuid4().hex[:8]}"
    m = Memory(
        id=mid,
        narrative="no auto index trigger",
        memory_type=MemoryType.FACTUAL,
        source="interaction",
    )
    cx.store(m)
    row = _query_memory_cols(cx, mid, "triggers")
    assert not row["triggers"].get("auto_index"), (
        f"non-reading source must not get auto_index, got: {row['triggers']}"
    )
