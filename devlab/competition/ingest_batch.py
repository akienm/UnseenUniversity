#!/usr/bin/env python3
"""
Ingest 10 programming books into the competition schema.

Selects the top-10 pending/queued programming books from clan.reading_list,
creates a competition campaign with target_schema=competition, enqueues
blocks for each book, and optionally runs the worker loop.

Usage:
    # Enqueue only (no extraction):
    python lab/competition/ingest_batch.py --enqueue-only

    # Enqueue + process (requires Ollama):
    python lab/competition/ingest_batch.py

    # Check campaign status:
    python lab/competition/ingest_batch.py --status
"""
from __future__ import annotations
from unseen_university.identity import home_db_url

import argparse
import os
import sys
from pathlib import Path

# Resolve repo root so devlab.claudecode imports work
_REPO = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "lab"))

import psycopg2

CAMPAIGN_ID = "competition-programming-batch-1"
TARGET_SCHEMA = "competition"
BUDGET_USD = 5.0
BOOK_COUNT = 10
CHUNK_SIZE = 15


def _select_books(limit: int = BOOK_COUNT) -> list[dict]:
    conn = psycopg2.connect(home_db_url())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, source, book_type "
                "FROM reading_list "
                "WHERE book_type = 'programming' "
                "  AND status IN ('pending', 'queued') "
                "ORDER BY id "
                "LIMIT %s",
                (limit,),
            )
            return [
                {"id": r[0], "title": r[1], "source": r[2], "book_type": r[3]}
                for r in cur.fetchall()
            ]
    finally:
        conn.close()


def run_enqueue() -> dict:
    from claudecode.reading_campaign import (
        create_campaign,
        enqueue_item_blocks,
    )

    books = _select_books(BOOK_COUNT)
    if not books:
        print("ERROR: no programming books found in reading_list")
        sys.exit(1)

    print(f"Selected {len(books)} programming books:")
    for i, b in enumerate(books, 1):
        print(f"  {i:2d}. [{b['id']}] {b['title']}")

    print(f"\nCreating campaign '{CAMPAIGN_ID}' (target_schema={TARGET_SCHEMA})...")
    result = create_campaign(
        CAMPAIGN_ID,
        budget_usd=BUDGET_USD,
        notes="competition batch 1 — programming books for classifier training",
        target_schema=TARGET_SCHEMA,
    )
    print(f"  → {result}")

    total_blocks = 0
    for priority, book in enumerate(books):
        # URL sources get a conservative chunk estimate; worker expands on first claim
        n_estimate = 8
        chunk_positions = [i * CHUNK_SIZE for i in range(n_estimate)]
        inserted = enqueue_item_blocks(
            campaign_id=CAMPAIGN_ID,
            reading_list_id=book["id"],
            item_source=book["source"],
            priority=priority,
            chunk_positions=chunk_positions,
            item_title=book["title"],
            item_author="",
        )
        total_blocks += inserted
        print(f"  enqueued {book['title'][:50]}: {inserted} blocks")

    print(f"\nTotal blocks queued: {total_blocks}")
    return {"campaign_id": CAMPAIGN_ID, "books": len(books), "blocks": total_blocks}


def run_status() -> None:
    from claudecode.reading_campaign import campaign_status, get_campaign_schema

    schema = get_campaign_schema(CAMPAIGN_ID)
    status = campaign_status(CAMPAIGN_ID)
    print(f"Campaign: {CAMPAIGN_ID} (target_schema={schema})")
    print(f"  Status:           {status['status']}")
    print(f"  Budget:           ${status['budget_usd']:.2f}")
    print(f"  Spent:            ${status['spent_usd']:.4f}")
    print(f"  Nodes deposited:  {status['nodes_deposited']}")
    print(f"  Blocks by status: {status['blocks_by_status']}")

    # Show competition.memories count
    conn = psycopg2.connect(home_db_url())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT memory_type, COUNT(*) FROM competition.memories "
                "GROUP BY memory_type ORDER BY memory_type"
            )
            rows = cur.fetchall()
            total = sum(r[1] for r in rows)
            print(f"\ncompetition.memories: {total} rows")
            for mtype, count in rows:
                print(f"  {mtype}: {count}")

            cur.execute("SELECT COUNT(*) FROM competition.memory_embeddings")
            emb_count = cur.fetchone()[0]
            print(f"competition.memory_embeddings: {emb_count} rows")
    finally:
        conn.close()


def run_worker() -> None:
    from claudecode.reading_campaign import worker_loop

    print(f"Starting worker loop for campaign '{CAMPAIGN_ID}'...")
    print("  (Ctrl+C to stop — progress is saved per block)")
    stats = worker_loop(
        CAMPAIGN_ID,
        idle_sleep_seconds=1.0,
        idle_max_iterations=5,
    )
    print(f"\nWorker finished: {stats}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--enqueue-only",
        action="store_true",
        help="Select books and enqueue blocks, but do not run extraction",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show campaign and competition.memories status",
    )
    args = parser.parse_args()

    if args.status:
        run_status()
        return

    run_enqueue()

    if not args.enqueue_only:
        run_worker()


if __name__ == "__main__":
    main()
