"""
test_cortex_delete_memory_fk_cascade.py — T-delete-memory-fk-cascade

Regression guard for the FK-cascade behaviour of cortex.delete_memory.
Prior to this fix, delete_memory ran a plain DELETE on memories; any row
with a companion memory_embeddings or memory_blobs entry failed the FK
constraint. NE's merge loop hit this on every merge attempt and flooded
the error pipeline at >200MB/sec disk write until Akien manually stopped
Igor 2026-04-24 PM.

Tests here run against the live Postgres instance (matches how other
cortex tests are wired). We deposit a tagged test memory + a synthetic
embedding row, delete, assert both gone. Teardown is handled by the
autouse test_data_lifecycle fixture.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


DB_URL = os.environ.get(
    "UU_HOME_DB_URL",
    "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
)


def _connect():
    import psycopg2

    return psycopg2.connect(DB_URL)


@pytest.fixture
def cortex():
    os.environ.setdefault("UU_HOME_DB_URL", DB_URL)
    from devices.igor.memory.cortex import Cortex

    return Cortex(None)


def _make_test_memory(cortex) -> str:
    """Deposit a tagged test memory, return its id."""
    from devices.igor.memory.models import Memory, MemoryType

    mem = Memory(
        narrative="test narrative for delete_memory FK cascade",
        memory_type=MemoryType.EPISODIC,
        metadata={"test_data": "true"},
    )
    stored = cortex.store(mem)
    return getattr(stored, "id", stored)


def _insert_embedding(memory_id: str, dim: int = 384) -> None:
    """Insert a synthetic embedding row keyed on memory_id."""
    import psycopg2

    vec = "[" + ",".join(["0.1"] * dim) + "]"
    with _connect() as conn, conn.cursor() as cur:
        # Schema varies; best-effort insert — skip if incompatible.
        try:
            cur.execute(
                "INSERT INTO memory_embeddings (memory_id, embedding) "
                "VALUES (%s, %s::vector) ON CONFLICT DO NOTHING",
                (memory_id, vec),
            )
            conn.commit()
        except psycopg2.Error:
            conn.rollback()
            pytest.skip("memory_embeddings schema incompatible with synthetic insert")


def _count_rows(table: str, memory_id: str) -> int:
    with _connect() as conn, conn.cursor() as cur:
        if table == "memories":
            cur.execute("SELECT COUNT(*) FROM memories WHERE id=%s", (memory_id,))
        else:
            cur.execute(
                f"SELECT COUNT(*) FROM {table} WHERE memory_id=%s", (memory_id,)
            )
        return cur.fetchone()[0]


# ── FK cascade ───────────────────────────────────────────────────────────────


def test_delete_memory_removes_embedding_row(cortex):
    """Prior bug: delete_memory ran DELETE FROM memories first and failed
    the memory_embeddings FK. After fix, both rows vanish together."""
    memory_id = _make_test_memory(cortex)
    _insert_embedding(memory_id)

    assert _count_rows("memories", memory_id) == 1
    assert _count_rows("memory_embeddings", memory_id) == 1

    deleted = cortex.delete_memory(memory_id)
    assert deleted is True
    assert _count_rows("memories", memory_id) == 0
    assert _count_rows("memory_embeddings", memory_id) == 0


def test_delete_memory_without_embedding_still_works(cortex):
    """A memory with no embedding row still deletes cleanly — the cascade
    must be a DELETE-if-any, not a require-embedding."""
    memory_id = _make_test_memory(cortex)
    assert _count_rows("memory_embeddings", memory_id) == 0

    deleted = cortex.delete_memory(memory_id)
    assert deleted is True
    assert _count_rows("memories", memory_id) == 0


def test_delete_memory_nonexistent_returns_false(cortex):
    """Deleting a nonexistent id is a no-op, returns False."""
    assert cortex.delete_memory("NONEXISTENT-FK-CASCADE-TEST-ID") is False


# ── Source regression guard ──────────────────────────────────────────────────


def test_delete_memory_source_deletes_children_first():
    """Regression guard: the source must delete from memory_embeddings and
    memory_blobs BEFORE memories. If someone reverts the order, NE merges
    would start failing again and flooding logs at MB/sec.
    """
    src = (
        Path(__file__).resolve().parent.parent.parent / "devices/igor/memory/cortex.py"
    ).read_text()
    # Locate delete_memory body and confirm ordering
    fn_idx = src.index("def delete_memory(self, memory_id: str)")
    next_def = src.index("\n    def ", fn_idx + 1)
    body = src[fn_idx:next_def]
    emb_idx = body.find("memory_embeddings")
    blobs_idx = body.find("memory_blobs")
    mem_idx = body.find("DELETE FROM memories")
    assert (
        emb_idx != -1 and blobs_idx != -1 and mem_idx != -1
    ), "delete_memory must reference all three tables"
    assert emb_idx < mem_idx, (
        "DELETE FROM memory_embeddings must happen before DELETE FROM memories "
        "(T-delete-memory-fk-cascade)"
    )
    assert (
        blobs_idx < mem_idx
    ), "DELETE FROM memory_blobs must happen before DELETE FROM memories"
