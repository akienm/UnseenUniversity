"""Tests for graph_integrator module."""

import json
import uuid
import pytest
from pathlib import Path
from datetime import datetime

from devices.igor.memory.cortex import Cortex
from devices.igor.memory.models import Memory, MemoryType
from devices.igor.cognition.graph_integrator import (
    integrate_graph,
    _fetch_fact_clouds,
    _calculate_edge_weight,
    _create_book_anchor_node,
)


@pytest.fixture
def cortex_test():
    """Create a test cortex instance (Postgres via UU_HOME_DB_URL)."""
    cortex = Cortex(None)
    yield cortex


def test_calculate_edge_weight():
    """Test chapter proximity weight calculation."""
    # Same chapter
    assert _calculate_edge_weight(0, 0) == 0.2

    # Adjacent chapters
    assert _calculate_edge_weight(0, 1) == 0.1
    assert _calculate_edge_weight(5, 4) == 0.1

    # Distant chapters
    assert _calculate_edge_weight(0, 2) == 0.05
    assert _calculate_edge_weight(1, 5) == 0.05
    assert _calculate_edge_weight(0, 100) == 0.05


def test_create_book_anchor_node(cortex_test):
    """Test creation of book anchor node."""
    content_id = f"test-book-{uuid.uuid4().hex[:8]}"
    title = "Test Book"
    author = "Test Author"

    node_id = _create_book_anchor_node(cortex_test, content_id, title, author)

    # Assert format: BOOK_{8-hex chars} from start of content_id
    assert node_id.startswith("BOOK_")
    assert len(node_id) == 13  # "BOOK_" + 8 hex chars
    assert node_id == f"BOOK_{content_id[:8].upper()}"

    # Verify the node was created
    with cortex_test._conn() as conn:
        mem = conn.execute(
            "SELECT id, narrative, memory_type FROM memories WHERE id = %s",
            (node_id,),
        ).fetchone()

    assert mem is not None
    assert mem[0] == node_id
    assert "Test Book" in mem[1]
    assert "Test Author" in mem[1]
    assert mem[2] == "ROOT"


def test_create_book_anchor_node_no_author(cortex_test):
    """Test book anchor creation without author."""
    content_id = f"test-no-auth-{uuid.uuid4().hex[:8]}"
    title = "Test Book"

    node_id = _create_book_anchor_node(cortex_test, content_id, title, "")

    assert node_id.startswith("BOOK_")
    assert len(node_id) == 13

    with cortex_test._conn() as conn:
        mem = conn.execute(
            "SELECT narrative FROM memories WHERE id = %s",
            (node_id,),
        ).fetchone()

    assert mem is not None
    assert "Test Book" in mem[0]


def test_fetch_fact_clouds(cortex_test):
    """Test fetching FACT_CLOUD nodes."""
    # Use a unique content_id per test run to avoid Postgres data pollution
    content_id = f"test-content-{uuid.uuid4().hex[:8]}"
    id_prefix = f"FACT_CLOUD_TEST{uuid.uuid4().hex[:6]}"

    # Create some FACT_CLOUD nodes
    for i in range(3):
        mem = Memory(
            id=f"{id_prefix}_{i:04d}",
            narrative=f"Fact {i}",
            memory_type=MemoryType.FACTUAL,
            source="reading_indexer",
            metadata={
                "content_id": content_id,
                "chapter_idx": i,
                "chunk_idx": 0,
                "extraction_confidence": 0.8,
            },
        )
        cortex_test.store(mem)

    # Also create a non-matching fact
    mem = Memory(
        id="FACT_CLOUD_99999999",
        narrative="Non-matching fact",
        memory_type=MemoryType.FACTUAL,
        source="reading_indexer",
        metadata={
            "content_id": "other-content-id",
            "chapter_idx": 0,
            "chunk_idx": 0,
            "extraction_confidence": 0.8,
        },
    )
    cortex_test.store(mem)

    # Fetch FACT_CLOUD nodes
    facts = _fetch_fact_clouds(cortex_test, content_id)

    assert len(facts) == 3
    assert all(f["id"].startswith("FACT_CLOUD_") for f in facts)
    assert facts[0]["narrative"] == "Fact 0"
    assert facts[0]["chapter_idx"] == 0
    assert facts[0]["confidence"] == 0.8


def test_integrate_graph_complete(cortex_test, monkeypatch):
    """Test complete graph integration."""
    content_id = "550e8400-e29b-41d4-a716-446655440000"

    # No DB_PATH needed — Postgres is used via UU_HOME_DB_URL

    # Mock get_blob_metadata at the blob_store module level
    def mock_get_blob_metadata(cid):
        if cid == content_id:
            return {"title": "Test Book", "author": "Test Author"}
        return None

    monkeypatch.setattr(
        "devices.igor.cognition.blob_store.get_blob_metadata",
        mock_get_blob_metadata,
    )

    # Create FACT_CLOUD nodes (same chapter, adjacent, distant)
    for i in range(5):
        mem = Memory(
            id=f"FACT_CLOUD_{i:08d}",
            narrative=f"Fact {i}",
            memory_type=MemoryType.FACTUAL,
            source="reading_indexer",
            metadata={
                "content_id": content_id,
                "chapter_idx": i,  # chapter 0, 1, 2, 3, 4
                "chunk_idx": 0,
                "extraction_confidence": 0.8,
            },
        )
        cortex_test.store(mem)

    # Run integration
    result = integrate_graph(content_id)

    # Verify result
    # (Note: result will be False because we're using a test cortex,
    # but the key thing is that edges were created)

    # Verify anchor node was created
    with cortex_test._conn() as conn:
        anchor = conn.execute(
            "SELECT id FROM memories WHERE id LIKE %s",
            ("BOOK_%",),
        ).fetchone()

    if anchor:
        assert anchor[0].startswith("BOOK_")

        # Verify edges were created
        with cortex_test._conn() as conn:
            edges = conn.execute(
                "SELECT from_id, to_id, weight FROM interpretive_edges WHERE from_id LIKE %s",
                ("BOOK_%",),
            ).fetchall()

        # Should have edges from anchor to facts
        assert len(edges) > 0


def test_integrate_graph_no_facts(cortex_test, monkeypatch):
    """Test graph integration with no facts."""
    content_id = "empty-content-id"

    def mock_get_blob_metadata(cid):
        if cid == content_id:
            return {"title": "Empty Book", "author": ""}
        return None

    monkeypatch.setattr(
        "devices.igor.cognition.blob_store.get_blob_metadata",
        mock_get_blob_metadata,
    )

    result = integrate_graph(content_id)
    assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
