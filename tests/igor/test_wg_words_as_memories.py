"""Integration tests for T-wg-words-as-memories: WORD_GRAPH memory type + get_or_create_word_memory."""

from __future__ import annotations

import pytest

from devices.igor.memory.cortex import Cortex
from devices.igor.memory.models import MemoryType


@pytest.fixture
def cortex():
    return Cortex()


def test_word_graph_type_exists():
    """MemoryType.WORD_GRAPH is defined and has value 'WORD_GRAPH'."""
    assert MemoryType.WORD_GRAPH.value == "WORD_GRAPH"


def test_word_graph_base_inertia():
    """WORD_GRAPH has a defined base inertia (should be very low)."""
    from devices.igor.memory.models import BASE_INERTIA
    assert MemoryType.WORD_GRAPH in BASE_INERTIA
    assert BASE_INERTIA[MemoryType.WORD_GRAPH] <= 0.10


def test_get_or_create_word_memory_creates_node(cortex):
    """get_or_create_word_memory creates a WORD_GRAPH memory and returns its ID."""
    word = "zymurgist_test_unique_word_xyz"
    memory_id = cortex.get_or_create_word_memory(word)
    assert memory_id, "Should return a non-empty ID"

    with cortex._conn() as conn:
        row = conn.execute(
            "SELECT id, memory_type, metadata->>'word' as word FROM memories WHERE id = %s",
            (memory_id,),
        ).fetchone()
    assert row is not None, "Memory should exist in DB"
    assert row["memory_type"] == "WORD_GRAPH"
    assert row["word"] == word


def test_get_or_create_word_memory_idempotent(cortex):
    """Calling get_or_create_word_memory twice with the same word returns the same ID."""
    word = "idempotent_test_word_alpha_beta"
    id1 = cortex.get_or_create_word_memory(word)
    id2 = cortex.get_or_create_word_memory(word)
    assert id1 == id2, "Second call must return same ID, not create a duplicate"

    with cortex._conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE memory_type='WORD_GRAPH' AND metadata->>'word' = %s",
            (word,),
        ).fetchone()
    assert row[0] == 1, "Exactly one WORD_GRAPH memory per word"


def test_word_graph_nodes_excluded_from_search(cortex):
    """WORD_GRAPH nodes do not appear in cortex.search() results."""
    word = "searchexclusion_test_word_gamma"
    cortex.get_or_create_word_memory(word)

    results = cortex.search(word, limit=20)
    result_ids = {m.id for m in results}

    with cortex._conn() as conn:
        row = conn.execute(
            "SELECT id FROM memories WHERE memory_type='WORD_GRAPH' AND metadata->>'word' = %s",
            (word,),
        ).fetchone()

    if row:
        assert row[0] not in result_ids, "WORD_GRAPH node must not appear in search() results"


def test_word_graph_node_has_stable_links_weighted(cortex):
    """After setting links_weighted on a WORD_GRAPH node, it persists correctly."""
    import json
    word = "links_test_word_delta"
    word2 = "links_test_word_epsilon"

    id1 = cortex.get_or_create_word_memory(word)
    id2 = cortex.get_or_create_word_memory(word2)

    links = {id2: 0.75}
    with cortex._conn() as conn:
        conn.execute(
            "UPDATE memories SET links_weighted = %s WHERE id = %s",
            (json.dumps(links), id1),
        )

    with cortex._conn() as conn:
        row = conn.execute(
            "SELECT links_weighted FROM memories WHERE id = %s",
            (id1,),
        ).fetchone()
    stored = json.loads(row[0]) if row else {}
    assert id2 in stored, "Target word ID should be in links_weighted"
    assert abs(stored[id2] - 0.75) < 0.001
