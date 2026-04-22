"""T-get-attractors-tree-walk: TTL cache for get_attractors().

Before this fix, cortex.get_attractors() ran a full-table scan on every
call — 891 hits, avg 181ms, worst 807ms in db_queries.log. After: result
cached per (db_path, limit) for _ATTRACTOR_CACHE_TTL_SEC seconds; repeat
calls within TTL skip the DB.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.memory import cortex as cortex_mod


@pytest.fixture(autouse=True)
def _clean_cache():
    cortex_mod._ATTRACTOR_CACHE.clear()
    yield
    cortex_mod._ATTRACTOR_CACHE.clear()


def _make_cortex_spy(db_path: str, id_rows: list[dict]):
    """Minimal stand-in: real Cortex is too heavy to instantiate in unit test.

    We construct an object that satisfies the method's references:
      - self.db_path, self._conn(), self.get(id)
    Calls to conn.execute().fetchall() return id_rows.
    """
    from wild_igor.igor.memory.cortex import Cortex

    c = Cortex.__new__(Cortex)
    c.db_path = db_path

    select_result = MagicMock()
    select_result.fetchall.return_value = id_rows

    conn_mock = MagicMock()
    conn_mock.execute.return_value = select_result
    conn_mock.__enter__ = lambda self: conn_mock
    conn_mock.__exit__ = lambda self, *a: False

    c._conn = MagicMock(return_value=conn_mock)
    c.get = MagicMock(side_effect=lambda mid: {"id": mid})

    c._conn_mock = conn_mock
    return c


def test_first_call_hits_db():
    cortex = _make_cortex_spy("/tmp/x.db", [{"id": "A"}, {"id": "B"}])
    result = cortex_mod.Cortex.get_attractors(cortex, limit=5)

    assert [m["id"] for m in result] == ["A", "B"]
    cortex._conn.assert_called_once()


def test_second_call_within_ttl_skips_db():
    cortex = _make_cortex_spy("/tmp/x.db", [{"id": "A"}])
    cortex_mod.Cortex.get_attractors(cortex, limit=5)
    cortex._conn.reset_mock()

    result = cortex_mod.Cortex.get_attractors(cortex, limit=5)

    assert [m["id"] for m in result] == ["A"]
    cortex._conn.assert_not_called()


def test_expired_ttl_refetches():
    import time as _time

    cortex = _make_cortex_spy("/tmp/x.db", [{"id": "A"}])
    cortex_mod.Cortex.get_attractors(cortex, limit=5)

    ts, result = cortex_mod._ATTRACTOR_CACHE[("/tmp/x.db", 5)]
    cortex_mod._ATTRACTOR_CACHE[("/tmp/x.db", 5)] = (
        ts - (cortex_mod._ATTRACTOR_CACHE_TTL_SEC + 1),
        result,
    )

    cortex._conn.reset_mock()
    cortex_mod.Cortex.get_attractors(cortex, limit=5)

    cortex._conn.assert_called_once()


def test_different_limits_cached_separately():
    cortex5 = _make_cortex_spy("/tmp/x.db", [{"id": "A"}])
    cortex_mod.Cortex.get_attractors(cortex5, limit=5)

    cortex20 = _make_cortex_spy("/tmp/x.db", [{"id": "B"}])
    cortex_mod.Cortex.get_attractors(cortex20, limit=20)
    cortex20._conn.assert_called_once()

    cortex20._conn.reset_mock()
    result = cortex_mod.Cortex.get_attractors(cortex20, limit=20)
    assert [m["id"] for m in result] == ["B"]
    cortex20._conn.assert_not_called()


def test_different_dbs_cached_separately():
    cortex_a = _make_cortex_spy("/tmp/a.db", [{"id": "A"}])
    cortex_mod.Cortex.get_attractors(cortex_a, limit=5)

    cortex_b = _make_cortex_spy("/tmp/b.db", [{"id": "B"}])
    result = cortex_mod.Cortex.get_attractors(cortex_b, limit=5)

    assert [m["id"] for m in result] == ["B"]
    cortex_b._conn.assert_called_once()
