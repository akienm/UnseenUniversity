"""
test_data_lifecycle.py — T-test-data-lifecycle

Tag + TTL + auto-cleanup for throwaway test fixtures. Honors the
memory_node_shape CORE_PRINCIPLE: all per-memory state lives in
metadata, never in new columns.

## The four mechanisms

1. **Tag** — `metadata.test_data = True` — hook for cleanup queries
2. **TTL** — `metadata.test_expires_at = <iso>` — self-degradation window
3. **Tree path** — nodes can be interpreted under a TEST_DATA index tree
   via interpretive_edges (out-of-scope MVP: indexes are separable from
   the per-node tag + ttl)
4. **pytest session fixture** — active enzyme, `cleanup_test_data()`
   runs at session teardown (see tests/conftest.py)

## Biomimetic framing

Tag = ubiquitin (marks protein for degradation).
TTL = protein half-life.
Cleanup fixture = autophagy enzyme.
Audit check = orphan detector.

## Env-var gating

`IGOR_TEST_MODE=1` in the environment switches cortex.store() into
auto-tagging mode. Set by tests/conftest.py at session start. Production
runs never set this, so the prod path is unchanged.

## Opt-out

Tests that want persistent state set metadata.test_data=False
explicitly — the tagger only stamps when the key is absent.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .cortex import Cortex

logger = logging.getLogger(__name__)


DEFAULT_TTL_SECONDS: int = 3600  # 1 hour default half-life
ENV_FLAG: str = "IGOR_TEST_MODE"


def is_test_mode() -> bool:
    """Check whether the environment is in test mode. Process-level, env-var
    driven so subprocesses inherit the flag naturally."""
    return os.environ.get(ENV_FLAG, "").strip() not in ("", "0", "false", "False")


def stamp_metadata_for_test_mode(
    metadata: dict[str, Any] | None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Return metadata with test_data=True and test_expires_at stamped.

    Pure: does not touch DB. Opt-out: if metadata already has
    test_data=False, respect it (intentional persistent test fixture).
    """
    md = dict(metadata or {})
    # Opt-out path: explicit False wins
    if md.get("test_data") is False:
        return md
    # Only stamp if the key is absent (don't clobber existing True)
    if "test_data" not in md:
        md["test_data"] = True
    if "test_expires_at" not in md:
        expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        md["test_expires_at"] = expires.isoformat()
    return md


# ── Cleanup ──────────────────────────────────────────────────────────────────


_TAGGED_SUBQUERY = (
    "SELECT id FROM memories "
    "WHERE jsonb_exists(metadata, 'test_data') "
    "  AND (metadata->>'test_data')::text = 'true'"
)

_EXPIRED_SUBQUERY = (
    "SELECT id FROM memories "
    "WHERE jsonb_exists(metadata, 'test_data') "
    "  AND (metadata->>'test_data')::text = 'true' "
    "  AND jsonb_exists(metadata, 'test_expires_at') "
    "  AND metadata->>'test_expires_at' < %s"
)


def _delete_dependents(conn, subquery: str, args: tuple = ()) -> None:
    """Delete from FK-referencing tables whose `memories` FK lacks ON DELETE
    CASCADE (memory_embeddings, trees). interpretive_edges + memory_blobs
    already cascade, so they're handled by the main DELETE.
    """
    conn.execute(f"DELETE FROM memory_embeddings WHERE memory_id IN ({subquery})", args)
    conn.execute(f"DELETE FROM trees WHERE facia_id IN ({subquery})", args)


def cleanup_test_data(cortex: "Cortex") -> int:
    """Delete every memory with metadata.test_data=true.

    Returns the number of rows removed. Uses jsonb_exists + boolean
    check per the db_proxy convention (don't use `metadata ? 'key'`).

    Deletes from memory_embeddings + trees first because their FKs to
    memories.id lack ON DELETE CASCADE. The CASCADE gap is a schema
    follow-up; cleaning in dependent order keeps us safe in the meantime.
    """
    deleted = 0
    try:
        with cortex._db() as conn:
            _delete_dependents(conn, _TAGGED_SUBQUERY)
            conn.execute(
                "DELETE FROM memories "
                "WHERE jsonb_exists(metadata, 'test_data') "
                "  AND (metadata->>'test_data')::text = 'true'"
            )
            deleted = getattr(conn, "rowcount", 0) or 0
    except Exception as exc:
        logger.warning("cleanup_test_data failed: %s", exc)
    return deleted


def reap_expired_test_data(cortex: "Cortex") -> int:
    """Delete test_data memories whose test_expires_at has passed.

    Useful for background reaping outside the session fixture (e.g.
    sleep consolidation pass, audit orphan sweep, cross-session crash
    recovery).
    """
    deleted = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with cortex._db() as conn:
            _delete_dependents(conn, _EXPIRED_SUBQUERY, (now_iso,))
            conn.execute(
                "DELETE FROM memories "
                "WHERE jsonb_exists(metadata, 'test_data') "
                "  AND (metadata->>'test_data')::text = 'true' "
                "  AND jsonb_exists(metadata, 'test_expires_at') "
                "  AND metadata->>'test_expires_at' < %s",
                (now_iso,),
            )
            deleted = getattr(conn, "rowcount", 0) or 0
    except Exception as exc:
        logger.warning("reap_expired_test_data failed: %s", exc)
    return deleted


def count_test_data(cortex: "Cortex") -> int:
    """Return the total number of test_data memories — for audit / reporting."""
    try:
        with cortex._db() as conn:
            conn.execute(
                "SELECT COUNT(*) FROM memories "
                "WHERE jsonb_exists(metadata, 'test_data') "
                "  AND (metadata->>'test_data')::text = 'true'"
            )
            row = conn.fetchone()
            return int(row[0]) if row else 0
    except Exception as exc:
        logger.warning("count_test_data failed: %s", exc)
        return 0


def count_orphan_test_data(cortex: "Cortex") -> int:
    """Return count of test_data memories past their TTL — audit signal."""
    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        with cortex._db() as conn:
            conn.execute(
                "SELECT COUNT(*) FROM memories "
                "WHERE jsonb_exists(metadata, 'test_data') "
                "  AND (metadata->>'test_data')::text = 'true' "
                "  AND jsonb_exists(metadata, 'test_expires_at') "
                "  AND metadata->>'test_expires_at' < %s",
                (now_iso,),
            )
            row = conn.fetchone()
            return int(row[0]) if row else 0
    except Exception as exc:
        logger.warning("count_orphan_test_data failed: %s", exc)
        return 0
