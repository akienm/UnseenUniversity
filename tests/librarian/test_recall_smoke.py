"""Smoke test: seed a memory, verify recall() surfaces it (real Postgres)."""

from __future__ import annotations

import os

import psycopg2
import pytest

from devices.librarian.memory_writer import write_memory
from devices.librarian.recall import recall

_DB_URL = os.environ.get("IGOR_HOME_DB_URL", "")

pytestmark = pytest.mark.skipif(
    not _DB_URL,
    reason="IGOR_HOME_DB_URL not set",
)

_CANARY = "librarian recall smoke test canary xray integration validation sentinel"


@pytest.fixture(scope="module")
def seeded_memory_id():
    result = write_memory(
        _CANARY,
        source_agent="cc/smoke-test",
        memory_type="FACTUAL",
        extra_tags=["smoke-test", "canary"],
        force_fallback=True,
    )
    memory_id = result["id"]
    assert not memory_id.startswith("db_error"), f"write_memory failed: {memory_id}"
    assert memory_id != "no_db", "write_memory: no DB configured"
    yield memory_id
    conn = psycopg2.connect(_DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM clan.memories WHERE id = %s", (memory_id,))
        conn.commit()
    finally:
        conn.close()


def test_recall_surfaces_seeded_memory(seeded_memory_id):
    result = recall("librarian recall smoke test canary", limit=20)
    assert len(result.hits) >= 1
    ids = [h.memory_id for h in result.hits]
    assert (
        seeded_memory_id in ids
    ), f"Seeded memory {seeded_memory_id!r} not in hits: {ids}"


def test_recall_hit_has_correct_fields(seeded_memory_id):
    result = recall("librarian recall smoke test canary", limit=20)
    hit = next((h for h in result.hits if h.memory_id == seeded_memory_id), None)
    assert hit is not None
    assert hit.narrative == _CANARY
    assert isinstance(hit.score, float)
    assert hit.source in {"fts", "vector", "graph"}
