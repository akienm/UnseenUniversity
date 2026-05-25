"""T-mem-cache-bounded-lru: Cortex._mem_cache LRU size cap.

Verifies that _cache_put evicts oldest non-genesis entries when the cache
exceeds _MEM_CACHE_MAX, and that genesis entries survive eviction.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.memory import cortex as cortex_mod
from devices.igor.memory.models import Memory, MemoryType


def _make_memory(id_: str, mtype: MemoryType = MemoryType.FACTUAL) -> Memory:
    return Memory(
        id=id_,
        memory_type=mtype,
        narrative=f"test {id_}",
    )


@pytest.fixture()
def cortex(tmp_path):
    """Cortex with a mocked DB — no Postgres needed."""
    with patch.object(
        cortex_mod, "make_home_proxy", return_value=MagicMock()
    ), patch.object(
        cortex_mod, "make_local_proxy", return_value=MagicMock()
    ), patch.object(
        cortex_mod.Cortex, "_init_db", return_value=None
    ):
        c = cortex_mod.Cortex(instance_id="test")
        yield c


class TestMemCacheBoundedLRU:
    def test_cap_holds_under_overflow(self, cortex):
        """Inserting 6000 non-genesis entries should not exceed _MEM_CACHE_MAX."""
        cap = cortex_mod._MEM_CACHE_MAX
        for i in range(cap + 1000):
            cortex._cache_put(_make_memory(f"m-{i}"))
        assert len(cortex._mem_cache) <= cap

    def test_oldest_evicted_first(self, cortex):
        """The first inserted entries should be evicted before recent ones."""
        cap = cortex_mod._MEM_CACHE_MAX
        first_id = "first-entry"
        cortex._cache_put(_make_memory(first_id))
        for i in range(cap):
            cortex._cache_put(_make_memory(f"fill-{i}"))
        assert first_id not in cortex._mem_cache

    def test_genesis_survives_eviction(self, cortex):
        """CORE_PATTERN (genesis) entries must not be evicted."""
        genesis_id = "genesis-node"
        cortex._cache_put(_make_memory(genesis_id, MemoryType.CORE_PATTERN))
        cap = cortex_mod._MEM_CACHE_MAX
        for i in range(cap + 500):
            cortex._cache_put(_make_memory(f"evictable-{i}"))
        assert genesis_id in cortex._mem_cache

    def test_cache_stats_shape(self, cortex):
        """cache_stats() returns expected keys."""
        cortex._cache_put(_make_memory("a"))
        cortex._cache_put(_make_memory("g", MemoryType.CORE_PATTERN))
        stats = cortex.cache_stats()
        assert set(stats) == {"size", "genesis", "evictable", "max"}
        assert stats["genesis"] == 1
        assert stats["evictable"] == 1
        assert stats["size"] == 2

    def test_get_moves_to_end(self, cortex):
        """Accessing an entry via _cache_get should keep it from being evicted."""
        cap = cortex_mod._MEM_CACHE_MAX
        keep_id = "keep-me"
        cortex._cache_put(_make_memory(keep_id))
        # Fill to almost cap so keep_id would normally be evicted next
        for i in range(cap - 1):
            cortex._cache_put(_make_memory(f"pad-{i}"))
        # Access keep_id to move it to end (most-recently-used)
        assert cortex._cache_get(keep_id) is not None
        # Push one more entry to trigger eviction of the new oldest
        cortex._cache_put(_make_memory("trigger-evict"))
        assert keep_id in cortex._mem_cache
