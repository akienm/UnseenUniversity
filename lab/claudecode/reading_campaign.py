"""
reading_campaign.py — T-reading-worker-pool

Stream-of-blocks queue for reading-list re-runs. A block is one chunk of
one item; workers claim the highest-priority block not in-flight, process
it via the existing reading pipeline, and ack back to the queue.

## Design

A reading CAMPAIGN is a named run over a prioritized subset of reading_list.
At campaign creation, items expand into BLOCKS (one per chunk). Workers
(currently: one per invocation — the existing cluster_router fans inference
across Ollama hosts implicitly) drain the queue:

    claim_next_block(campaign_id) → process → mark_done | mark_failed

Cloud retry: on local failure, if the campaign still has cloud budget
remaining, the block is retried via Sonnet (OR tier.4). One retry only.
If Sonnet also fails, the block is marked failed and surfaces for Akien+CC
review.

## Tables

- infra.reading_campaigns — budget envelope + status per named run
- infra.reading_blocks    — work queue: (item, chunk_pos, priority, status,
                            attempt, model_used, tier, cost_usd, ...)

Both schemas are created on first use via _ensure_schema().

## Non-goals (follow-ups)

- Multi-machine orchestration. cluster_router already fans across hosts;
  a single worker loop is enough. If we need explicit per-box workers later,
  add a subprocess pool on top.
- Block-level resume after worker death. Visibility timeout + reclaim is a
  ticket; today's assumption: worker finishes or you restart.
- Cost attribution to items beyond the rollup this module provides.

## Relationship to reading_tool / reading_engine

reading_tool.py still owns the item-level RUN/ITEM lifecycle (reading_runs,
reading_run_items). This module adds the BLOCK layer underneath — once a
campaign is running, each block delegates its actual extraction to
reading_engine.process_blob with limit=1 (one chunk at a time), which then
calls book_learner._extract_nodes / _deposit_nodes through the existing
infrastructure.

## Decisions shaping this
- Akien 2026-04-18/04-19: stream of blocks, priority per master list,
  local-first with cloud fallback on one retry, $40 envelope, per-item
  cost tracking.
- Collapses to a single local model (qwen2.5:7b) — no batch/light split
  at the worker level.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ── Schema ───────────────────────────────────────────────────────────────────

_SCHEMA_CAMPAIGNS = """
CREATE TABLE IF NOT EXISTS infra.reading_campaigns (
    campaign_id   TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    budget_usd    NUMERIC(10, 2) NOT NULL,
    spent_usd     NUMERIC(12, 6) DEFAULT 0,
    status        TEXT DEFAULT 'active',
    notes         TEXT,
    target_schema TEXT DEFAULT 'clan'
)
"""

_MIGRATE_CAMPAIGNS_ADD_SCHEMA = """
ALTER TABLE infra.reading_campaigns
    ADD COLUMN IF NOT EXISTS target_schema TEXT DEFAULT 'clan'
"""

_SCHEMA_BLOCKS = """
CREATE TABLE IF NOT EXISTS infra.reading_blocks (
    id               BIGSERIAL PRIMARY KEY,
    campaign_id      TEXT NOT NULL REFERENCES infra.reading_campaigns(campaign_id),
    reading_list_id  TEXT NOT NULL,
    item_source      TEXT NOT NULL,       -- URL or calibre:// reference
    item_title       TEXT,
    item_author      TEXT,
    chunk_pos        INTEGER NOT NULL,    -- 0, chunk_size, 2*chunk_size ...
    priority         INTEGER NOT NULL,    -- from master list order; lower = higher
    status           TEXT DEFAULT 'queued',
    claimed_by       TEXT,
    claimed_at       TEXT,
    completed_at     TEXT,
    attempt_count    INTEGER DEFAULT 0,
    last_error       TEXT,
    model_used       TEXT,
    inference_tier   TEXT,
    nodes_deposited  INTEGER DEFAULT 0,
    cost_usd         NUMERIC(12, 6) DEFAULT 0,
    created_at       TEXT NOT NULL,
    UNIQUE (campaign_id, reading_list_id, chunk_pos)
)
"""

_SCHEMA_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_reading_blocks_queue ON infra.reading_blocks(campaign_id, status, priority, id)",
    "CREATE INDEX IF NOT EXISTS idx_reading_blocks_item ON infra.reading_blocks(reading_list_id)",
]


def _conn():
    import psycopg2

    return psycopg2.connect(os.environ["UU_HOME_DB_URL"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schema() -> None:
    """Create tables + indexes if missing. Idempotent."""
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO infra,clan,public")
                cur.execute(_SCHEMA_CAMPAIGNS)
                cur.execute(_MIGRATE_CAMPAIGNS_ADD_SCHEMA)
                cur.execute(_SCHEMA_BLOCKS)
                for idx in _SCHEMA_INDEXES:
                    cur.execute(idx)
    finally:
        conn.close()


# ── Campaign lifecycle ───────────────────────────────────────────────────────


def create_campaign(
    campaign_id: str,
    budget_usd: float,
    notes: str = "",
    target_schema: str = "clan",
) -> dict:
    """Create a campaign envelope. Idempotent on campaign_id.

    target_schema controls where extracted memories are deposited.
    Defaults to 'clan' (production). Pass 'competition' to route to the
    isolated competition schema (T-competition-pipeline-configurable).
    """
    _ensure_schema()
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO infra")
                cur.execute(
                    "INSERT INTO reading_campaigns "
                    "  (campaign_id, created_at, budget_usd, notes, target_schema) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (campaign_id) DO UPDATE SET "
                    "  budget_usd = EXCLUDED.budget_usd, "
                    "  notes = EXCLUDED.notes, "
                    "  target_schema = EXCLUDED.target_schema",
                    (campaign_id, _now_iso(), budget_usd, notes, target_schema),
                )
    finally:
        conn.close()
    return {"campaign_id": campaign_id, "budget_usd": budget_usd, "target_schema": target_schema}


def get_campaign_schema(campaign_id: str) -> str:
    """Return the target_schema for a campaign (default: 'clan')."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path TO infra")
            cur.execute(
                "SELECT COALESCE(target_schema, 'clan') FROM reading_campaigns "
                "WHERE campaign_id = %s",
                (campaign_id,),
            )
            row = cur.fetchone()
            return row[0] if row else "clan"
    finally:
        conn.close()


def campaign_spent(campaign_id: str) -> float:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path TO infra")
            cur.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM reading_blocks WHERE campaign_id = %s",
                (campaign_id,),
            )
            row = cur.fetchone()
            return float(row[0]) if row else 0.0
    finally:
        conn.close()


def campaign_budget_remaining(campaign_id: str) -> float:
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path TO infra")
            cur.execute(
                "SELECT budget_usd FROM reading_campaigns WHERE campaign_id = %s",
                (campaign_id,),
            )
            row = cur.fetchone()
            if not row:
                return 0.0
            budget = float(row[0])
    finally:
        conn.close()
    return max(0.0, budget - campaign_spent(campaign_id))


# ── Block queue ──────────────────────────────────────────────────────────────


def enqueue_item_blocks(
    campaign_id: str,
    reading_list_id: str,
    item_source: str,
    priority: int,
    chunk_positions: list[int],
    item_title: str = "",
    item_author: str = "",
) -> int:
    """Insert block rows for a single item. Each chunk_pos becomes one block.

    Idempotent on (campaign_id, reading_list_id, chunk_pos) — re-enqueue is
    a no-op for already-queued chunks.
    """
    _ensure_schema()
    now = _now_iso()
    conn = _conn()
    inserted = 0
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO infra")
                for cp in chunk_positions:
                    cur.execute(
                        "INSERT INTO reading_blocks "
                        "(campaign_id, reading_list_id, item_source, item_title, item_author, "
                        " chunk_pos, priority, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (campaign_id, reading_list_id, chunk_pos) DO NOTHING",
                        (
                            campaign_id,
                            reading_list_id,
                            item_source,
                            item_title,
                            item_author,
                            cp,
                            priority,
                            now,
                        ),
                    )
                    if cur.rowcount > 0:
                        inserted += 1
    finally:
        conn.close()
    return inserted


def claim_next_block(campaign_id: str, worker_id: str) -> Optional[dict]:
    """Atomically claim the highest-priority queued block for this campaign.

    Uses SELECT ... FOR UPDATE SKIP LOCKED so concurrent workers don't
    step on each other. Returns block dict or None if queue is empty.
    """
    _ensure_schema()
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO infra")
                cur.execute(
                    "SELECT id, campaign_id, reading_list_id, item_source, item_title, "
                    "item_author, chunk_pos, priority, attempt_count "
                    "FROM reading_blocks "
                    "WHERE campaign_id = %s AND status = 'queued' "
                    "ORDER BY priority ASC, id ASC "
                    "LIMIT 1 FOR UPDATE SKIP LOCKED",
                    (campaign_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                block_id = row[0]
                cur.execute(
                    "UPDATE reading_blocks SET status = 'claimed', claimed_by = %s, "
                    "claimed_at = %s, attempt_count = attempt_count + 1 "
                    "WHERE id = %s",
                    (worker_id, _now_iso(), block_id),
                )
                return {
                    "id": block_id,
                    "campaign_id": row[1],
                    "reading_list_id": row[2],
                    "item_source": row[3],
                    "item_title": row[4],
                    "item_author": row[5],
                    "chunk_pos": row[6],
                    "priority": row[7],
                    "attempt_count": row[8] + 1,
                }
    finally:
        conn.close()


def mark_block_done(
    block_id: int,
    nodes_deposited: int,
    model_used: str,
    inference_tier: str,
    cost_usd: float = 0.0,
) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO infra")
                cur.execute(
                    "UPDATE reading_blocks SET status = 'done', completed_at = %s, "
                    "nodes_deposited = %s, model_used = %s, inference_tier = %s, cost_usd = %s "
                    "WHERE id = %s",
                    (
                        _now_iso(),
                        nodes_deposited,
                        model_used,
                        inference_tier,
                        cost_usd,
                        block_id,
                    ),
                )
    finally:
        conn.close()


def mark_block_failed(block_id: int, error_message: str) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO infra")
                cur.execute(
                    "UPDATE reading_blocks SET status = 'failed', completed_at = %s, "
                    "last_error = %s "
                    "WHERE id = %s",
                    (_now_iso(), error_message[:500], block_id),
                )
    finally:
        conn.close()


def mark_block_retry_cloud(block_id: int, local_error: str) -> None:
    """Move a block from claimed → retry_cloud state; subsequent worker loop
    iteration picks it up for the Sonnet retry attempt.
    """
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO infra")
                cur.execute(
                    "UPDATE reading_blocks SET status = 'queued', last_error = %s, "
                    "claimed_by = NULL, claimed_at = NULL "
                    "WHERE id = %s",
                    (f"LOCAL_FAIL: {local_error[:480]}", block_id),
                )
    finally:
        conn.close()


# ── Status / reporting ───────────────────────────────────────────────────────


def campaign_status(campaign_id: str) -> dict:
    """Return a rollup: block counts by status, spend, nodes, per-item stats."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path TO infra")
            cur.execute(
                "SELECT status, COUNT(*) FROM reading_blocks "
                "WHERE campaign_id = %s GROUP BY status",
                (campaign_id,),
            )
            by_status = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute(
                "SELECT COALESCE(SUM(cost_usd), 0), COALESCE(SUM(nodes_deposited), 0) "
                "FROM reading_blocks WHERE campaign_id = %s",
                (campaign_id,),
            )
            totals = cur.fetchone()
            spent = float(totals[0]) if totals else 0.0
            nodes = int(totals[1]) if totals else 0
            cur.execute(
                "SELECT budget_usd, status FROM reading_campaigns WHERE campaign_id = %s",
                (campaign_id,),
            )
            meta = cur.fetchone()
            budget = float(meta[0]) if meta else 0.0
            camp_status = meta[1] if meta else "unknown"
    finally:
        conn.close()
    return {
        "campaign_id": campaign_id,
        "status": camp_status,
        "budget_usd": budget,
        "spent_usd": spent,
        "remaining_usd": max(0.0, budget - spent),
        "nodes_deposited": nodes,
        "blocks_by_status": by_status,
    }


def item_cost_rollup(campaign_id: str, limit: int = 20) -> list[dict]:
    """Top-N items by cost in this campaign. For 'how much did book X cost?'."""
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SET search_path TO infra")
            cur.execute(
                "SELECT reading_list_id, item_title, "
                "       COALESCE(SUM(cost_usd), 0) AS cost, "
                "       COALESCE(SUM(nodes_deposited), 0) AS nodes, "
                "       COUNT(*) AS total_blocks, "
                "       COUNT(*) FILTER (WHERE status = 'done') AS done_blocks "
                "FROM reading_blocks "
                "WHERE campaign_id = %s "
                "GROUP BY reading_list_id, item_title "
                "ORDER BY cost DESC "
                "LIMIT %s",
                (campaign_id, limit),
            )
            return [
                {
                    "reading_list_id": r[0],
                    "item_title": r[1],
                    "cost_usd": float(r[2]),
                    "nodes_deposited": int(r[3]),
                    "total_blocks": int(r[4]),
                    "done_blocks": int(r[5]),
                }
                for r in cur.fetchall()
            ]
    finally:
        conn.close()


# ── Master-list expansion ────────────────────────────────────────────────────


def _parse_master_list(path: Path) -> list[dict]:
    """Parse an Akien-style master reading list file.

    Recognizes:
      [ ] [calibre] Title (calibre://NNN)
      [ ] [url] Title — https://...
      [ ] [file] Title (local file path)
      [ ] [code] repo path

    Returns a list of {priority, type, source, title} in FILE ORDER (so
    priority == index in the returned list). Lines already marked [x]
    are treated as complete and excluded.
    """
    items: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.rstrip()
        if not line.startswith("[ ]"):
            continue
        # After "[ ] [type] " comes content
        rest = line[3:].lstrip()
        if not rest.startswith("["):
            continue
        type_end = rest.find("]", 1)
        if type_end < 0:
            continue
        item_type = rest[1:type_end].strip()
        content = rest[type_end + 1 :].strip()
        # Source extraction varies by type
        source = ""
        title = content
        if item_type == "calibre":
            # "Title (calibre://NNN)"
            open_par = content.rfind("(calibre://")
            if open_par >= 0:
                close_par = content.find(")", open_par)
                if close_par > open_par:
                    source = content[open_par + 1 : close_par]
                    title = content[:open_par].strip(" —–-")
        elif item_type == "url":
            # "Title — https://..."
            if " — " in content:
                parts = content.rsplit(" — ", 1)
                title, source = parts[0], parts[1]
            elif " - " in content:
                parts = content.rsplit(" - ", 1)
                title, source = parts[0], parts[1]
            elif content.startswith("http"):
                source = content
                title = ""
        elif item_type == "file":
            # Filepath form; title is rest
            source = content
        elif item_type == "code":
            source = content
        else:
            continue
        if not source:
            continue
        items.append(
            {
                "priority": len(items),
                "type": item_type,
                "source": source,
                "title": title[:200],
            }
        )
    return items


# Per-type chunk count estimate for lazy pre-expansion.
# Workers discover the real count at claim-time via count_chunks; any
# over-estimate just produces blocks that get marked 'done' with node_count=0
# when processed (harmless). Under-estimate is worse — means workers expand
# additional blocks at run time.
_CHUNK_ESTIMATE = {
    "url": 8,
    "calibre": 200,
    "file": 100,
    "code": 50,
}


def expand_campaign_from_master_list(
    campaign_id: str,
    master_list_path: str | Path,
) -> dict:
    """Expand the master reading list into queued blocks.

    Each item gets _CHUNK_ESTIMATE[type] chunk rows inserted. On first
    worker-claim of chunk_pos=0 for an item, the worker fetches the
    blob and either (a) confirms the estimate, or (b) inserts any
    missing blocks if the real count exceeds the estimate.

    Idempotent: re-running is safe (ON CONFLICT DO NOTHING on the
    unique index).
    """
    _ensure_schema()
    items = _parse_master_list(Path(master_list_path))
    total_inserted = 0
    for item in items:
        n = _CHUNK_ESTIMATE.get(item["type"], 10)
        chunk_positions = [i * 15 for i in range(n)]  # chunk_size=15
        inserted = enqueue_item_blocks(
            campaign_id=campaign_id,
            reading_list_id=item["source"],  # use source as stable ID
            item_source=item["source"],
            priority=item["priority"],
            chunk_positions=chunk_positions,
            item_title=item["title"],
            item_author="",
        )
        total_inserted += inserted
    return {
        "campaign_id": campaign_id,
        "items_parsed": len(items),
        "blocks_inserted": total_inserted,
    }


# ── Worker loop ──────────────────────────────────────────────────────────────


def _worker_id() -> str:
    """Stable-ish identifier for the current worker (host:pid)."""
    import socket

    return f"{socket.gethostname()}:{os.getpid()}"


def _blob_for(item_source: str, title: str, author: str) -> Path:
    """Return the blob path for an item, fetching it if absent."""
    from devices.igor.tools.reading_engine import blob_path as _bp
    from devices.igor.tools.reading_engine import fetch_to_blob as _fetch

    bp = _bp(item_source)
    if not bp.exists():
        _fetch(item_source, title=title, author=author)
    return bp


def _process_block_local(block: dict, target_schema: str = "clan") -> dict:
    """Process a block via the existing local qwen path. Returns result dict.

    Raises on hard failure; caller handles retry policy.
    """
    from devices.igor.tools.reading_engine import process_one_chunk

    blob = _blob_for(block["item_source"], block["item_title"], block["item_author"])
    return process_one_chunk(
        blob_file=blob,
        chunk_pos=block["chunk_pos"],
        pass_number=1,
        use_local=True,
        target_schema=target_schema,
    )


def _process_block_cloud_retry(
    block: dict, model: str = "anthropic/claude-sonnet-4", target_schema: str = "clan"
) -> dict:
    """One-retry path via cloud Sonnet. Returns result dict.

    Called only when the local attempt failed AND the campaign still has
    budget remaining. Cost from this call is attributed back to the block.
    """
    from devices.igor.tools.reading_engine import process_one_chunk

    blob = _blob_for(block["item_source"], block["item_title"], block["item_author"])
    return process_one_chunk(
        blob_file=blob,
        chunk_pos=block["chunk_pos"],
        pass_number=1,
        use_local=False,
        model=model,
        target_schema=target_schema,
    )


def worker_loop(
    campaign_id: str,
    max_blocks: int | None = None,
    sonnet_retry_model: str = "anthropic/claude-sonnet-4",
    idle_sleep_seconds: float = 2.0,
    idle_max_iterations: int = 3,
) -> dict:
    """Single-worker drain loop for a campaign.

    cluster_router inside _extract_nodes_local already fans across online
    Ollama boxes, so one worker loop parallelizes implicitly — no subprocess
    orchestration needed at this layer.

    Exits when: queue empty for `idle_max_iterations` consecutive idle ticks,
    OR max_blocks processed, OR budget exhausted (cloud path only).

    Returns rollup dict: processed_local, processed_cloud, failed, skipped_budget.
    """
    worker = _worker_id()
    target_schema = get_campaign_schema(campaign_id)
    stats = {
        "processed_local": 0,
        "processed_cloud": 0,
        "failed": 0,
        "skipped_budget": 0,
        "idle_exits": 0,
        "target_schema": target_schema,
    }
    idle_count = 0
    processed_total = 0

    while True:
        if max_blocks is not None and processed_total >= max_blocks:
            break

        block = claim_next_block(campaign_id, worker)
        if block is None:
            idle_count += 1
            if idle_count >= idle_max_iterations:
                stats["idle_exits"] += 1
                break
            time.sleep(idle_sleep_seconds)
            continue
        idle_count = 0

        # On chunk_pos==0 (first chunk of an item), expand additional chunks
        # from real blob size. Idempotent — existing rows stay queued.
        if block["chunk_pos"] == 0:
            try:
                from devices.igor.tools.reading_engine import blob_path as _bp
                from devices.igor.tools.reading_engine import count_chunks as _cc

                bp = _bp(block["item_source"])
                if bp.exists():
                    real_n = _cc(bp)
                    if real_n > _CHUNK_ESTIMATE.get("calibre", 200):
                        extra = [
                            i * 15 for i in range(_CHUNK_ESTIMATE["calibre"], real_n)
                        ]
                        enqueue_item_blocks(
                            campaign_id=campaign_id,
                            reading_list_id=block["reading_list_id"],
                            item_source=block["item_source"],
                            priority=block["priority"],
                            chunk_positions=extra,
                            item_title=block["item_title"],
                            item_author=block["item_author"],
                        )
            except Exception as exc:
                log.warning("lazy-expand failed for %s: %s", block["item_source"], exc)

        # Local attempt
        try:
            result = _process_block_local(block, target_schema=target_schema)
            if result.get("at_end"):
                # Empty/past-end block: mark done with 0 nodes, no cost
                mark_block_done(
                    block["id"],
                    nodes_deposited=0,
                    model_used=result.get("model_used", "") or "n/a",
                    inference_tier=result.get("inference_tier", "") or "n/a",
                    cost_usd=0.0,
                )
                stats["processed_local"] += 1
                processed_total += 1
                continue
            mark_block_done(
                block["id"],
                nodes_deposited=int(result.get("node_count", 0)),
                model_used=result.get("model_used", ""),
                inference_tier=result.get("inference_tier", "local"),
                cost_usd=0.0,  # local inference is free
            )
            stats["processed_local"] += 1
            processed_total += 1
            continue
        except Exception as local_exc:
            local_err = f"{type(local_exc).__name__}: {local_exc}"
            log.info("block %d local failed: %s", block["id"], local_err)

        # Cloud retry (gated by budget)
        remaining = campaign_budget_remaining(campaign_id)
        if remaining <= 0:
            mark_block_failed(
                block["id"],
                f"local_fail + budget exhausted: {local_err}",
            )
            stats["skipped_budget"] += 1
            processed_total += 1
            continue

        try:
            result = _process_block_cloud_retry(block, model=sonnet_retry_model, target_schema=target_schema)
            # Crude cost estimate — Sonnet is ~$3/MTok input, ~$15/MTok output
            # A chunk is ~800 tokens in, ~300 out = ~$0.007 per chunk.
            est_cost = 0.007
            if remaining - est_cost < 0:
                # Would blow the budget; skip the cloud call instead
                mark_block_failed(
                    block["id"],
                    f"local_fail + would exceed budget (est ${est_cost:.4f})",
                )
                stats["skipped_budget"] += 1
                processed_total += 1
                continue
            mark_block_done(
                block["id"],
                nodes_deposited=int(result.get("node_count", 0)),
                model_used=result.get("model_used", sonnet_retry_model),
                inference_tier="cloud",
                cost_usd=est_cost,
            )
            stats["processed_cloud"] += 1
            processed_total += 1
        except Exception as cloud_exc:
            mark_block_failed(
                block["id"],
                f"local_fail: {local_err}; cloud_fail: {type(cloud_exc).__name__}: {cloud_exc}",
            )
            stats["failed"] += 1
            processed_total += 1

    return stats


def main():
    """CLI entry point:  python3 reading_campaign.py <campaign_id> [max_blocks]."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: reading_campaign.py <campaign_id> [max_blocks]")
        sys.exit(1)
    campaign_id = sys.argv[1]
    max_blocks = int(sys.argv[2]) if len(sys.argv) > 2 else None
    stats = worker_loop(campaign_id, max_blocks=max_blocks)
    print(f"worker_loop done: {stats}")
    report = campaign_status(campaign_id)
    print(f"campaign status: {json.dumps(report, indent=2)}")


if __name__ == "__main__":
    main()
