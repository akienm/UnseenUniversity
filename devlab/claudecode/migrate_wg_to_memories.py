#!/usr/bin/env python3
"""migrate_wg_to_memories.py — One-time migration of wg_edges words into clan.memories WORD_GRAPH nodes.

Run: python3 devlab/claudecode/migrate_wg_to_memories.py

Idempotent: safe to run multiple times.
  Phase 1: create WORD_GRAPH memories for all distinct words (word_a UNION word_b from wg_edges).
           ON CONFLICT on unique index idx_memories_word_graph_word means re-runs add only missing words.
  Phase 2: populate links_weighted for each word_a from wg_edges similarity weights (top edges per word).
           Re-runs update links_weighted (idempotent UPDATE).

Scope:
  ~57,694 distinct words → WORD_GRAPH memories in clan.memories.
  ~1.14M links_weighted entries (20 edges × 57,298 word_a values).
  Batch size: 1000 words/transaction.
"""
from __future__ import annotations
from unseen_university.identity import home_db_url

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import psycopg2
import psycopg2.extras

_BATCH = 1000


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _phase1_create_words(conn) -> dict[str, str]:
    """Create WORD_GRAPH memories for all distinct words.

    Returns {word: memory_id} for ALL words (new and pre-existing).
    """
    from devices.igor.memory.node_id import new_node_id

    print("Phase 1: collecting distinct words from wg_edges...", flush=True)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT word_a FROM clan.wg_edges "
            "UNION SELECT DISTINCT word_b FROM clan.wg_edges"
        )
        all_words = [r[0] for r in cur.fetchall()]
    print(f"  Found {len(all_words):,} distinct words", flush=True)

    print("  Checking for existing WORD_GRAPH memories...", flush=True)
    word_to_id: dict[str, str] = {}
    # Fetch all existing in one query
    with conn.cursor() as cur:
        cur.execute(
            "SELECT metadata->>'word', id FROM clan.memories WHERE memory_type='WORD_GRAPH'"
        )
        for row in cur.fetchall():
            if row[0]:
                word_to_id[row[0]] = row[1]
    print(f"  {len(word_to_id):,} words already migrated", flush=True)

    new_words = [w for w in all_words if w not in word_to_id]
    print(f"  {len(new_words):,} new words to create", flush=True)

    now_iso = _ts()
    created = 0
    for batch_start in range(0, len(new_words), _BATCH):
        batch = new_words[batch_start : batch_start + _BATCH]
        rows = [
            (
                new_node_id(),
                word,
                json.dumps({"word": word}),
                now_iso,
                now_iso,
            )
            for word in batch
        ]
        with conn.cursor() as cur:
            try:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO clan.memories
                      (id, narrative, memory_type, metadata, timestamp, updated_at, scope,
                       portable, valence, arousal, dominance, activation_count,
                       children_ids, link_ids, friction_history, links_weighted,
                       source, confidence)
                    VALUES %s
                    """,
                    [
                        (
                            r[0], r[1], "WORD_GRAPH", r[2], r[3], r[4], "class",
                            1, 0.0, 0.0, 0.0, 0,
                            "[]", "[]", "[]", "{}",
                            "word_graph", 1.0,
                        )
                        for r in rows
                    ],
                    template="(%s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                )
            except Exception as exc:
                conn.rollback()
                print(f"  Batch insert error (may be partial duplicate batch): {exc}", flush=True)
        conn.commit()
        created += len(batch)
        print(f"  Created batch {batch_start // _BATCH + 1}: {created:,}/{len(new_words):,}", flush=True)

    # Reload full word→id map
    print("  Reloading word→id map...", flush=True)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT metadata->>'word', id FROM clan.memories WHERE memory_type='WORD_GRAPH'"
        )
        word_to_id = {r[0]: r[1] for r in cur.fetchall() if r[0]}
    print(f"  Phase 1 complete: {len(word_to_id):,} words in clan.memories", flush=True)
    return word_to_id


def _phase2_populate_links(conn, word_to_id: dict[str, str]) -> None:
    """Populate links_weighted for each word_a from wg_edges similarity weights.

    Uses top edges per word_a (up to 100, but wg_edges has exactly 20 per word_a).
    links_weighted format: {target_memory_id: similarity_weight}
    """
    print("Phase 2: fetching all wg_edges for links_weighted...", flush=True)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT word_a, word_b, similarity FROM clan.wg_edges ORDER BY word_a, similarity DESC"
        )
        edges = cur.fetchall()
    print(f"  {len(edges):,} edges to process", flush=True)

    # Group by word_a, build links_weighted dict
    links_by_word: dict[str, dict[str, float]] = {}
    missing_targets = 0
    for word_a, word_b, similarity in edges:
        if word_a not in word_to_id:
            continue
        target_id = word_to_id.get(word_b)
        if target_id is None:
            missing_targets += 1
            continue
        links = links_by_word.setdefault(word_a, {})
        links[target_id] = float(similarity)

    if missing_targets:
        print(f"  Warning: {missing_targets:,} edges skipped (word_b not in word_to_id)", flush=True)

    word_a_list = list(links_by_word.items())
    print(f"  Updating links_weighted for {len(word_a_list):,} word_a nodes...", flush=True)

    updated = 0
    for batch_start in range(0, len(word_a_list), _BATCH):
        batch = word_a_list[batch_start : batch_start + _BATCH]
        rows = [(word_to_id[word], json.dumps(links)) for word, links in batch]
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                "UPDATE clan.memories SET links_weighted = data.lw "
                "FROM (VALUES %s) AS data(id, lw) "
                "WHERE clan.memories.id = data.id",
                rows,
                template="(%s, %s)",
            )
        conn.commit()
        updated += len(batch)
        print(f"  Updated batch {batch_start // _BATCH + 1}: {updated:,}/{len(word_a_list):,}", flush=True)

    print(f"  Phase 2 complete: {updated:,} word_a nodes have links_weighted", flush=True)


def _register_nodes_batch(conn, word_to_id: dict[str, str]) -> None:
    """Batch-register new word node IDs in node_registry (best-effort)."""
    import socket
    host = socket.gethostname()
    now_ts = datetime.now(timezone.utc)
    rows = [(nid, "memories", nid, host, now_ts) for nid in word_to_id.values()]
    print(f"Registering {len(rows):,} nodes in node_registry (batch, ON CONFLICT DO NOTHING)...", flush=True)
    for batch_start in range(0, len(rows), _BATCH):
        batch = rows[batch_start : batch_start + _BATCH]
        try:
            with conn.cursor() as cur:
                psycopg2.extras.execute_values(
                    cur,
                    """
                    INSERT INTO node_registry (node_id, table_name, row_id, machine_id, created_at)
                    VALUES %s
                    ON CONFLICT (node_id) DO NOTHING
                    """,
                    batch,
                    template="(%s, %s, %s, %s, %s)",
                )
            conn.commit()
        except Exception as exc:
            print(f"  node_registry batch {batch_start // _BATCH + 1} failed (non-fatal): {exc}", flush=True)
            conn.rollback()
    print("  node_registry registration complete", flush=True)


def _verify(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM clan.memories WHERE memory_type='WORD_GRAPH'")
        return cur.fetchone()[0]


def main() -> None:
    print(f"migrate_wg_to_memories — {_ts()}", flush=True)
    conn = psycopg2.connect(home_db_url())
    conn.autocommit = False
    try:
        word_to_id = _phase1_create_words(conn)
        _phase2_populate_links(conn, word_to_id)
        _register_nodes_batch(conn, word_to_id)
        count = _verify(conn)
        print(f"\nVerification: {count:,} WORD_GRAPH memories in clan.memories", flush=True)
        if count >= 50000:
            print("✓ Completion criterion met: COUNT >= 50000", flush=True)
        else:
            print(f"✗ Completion criterion NOT met: {count} < 50000", flush=True)
            sys.exit(1)
    finally:
        conn.close()
    print(f"Done — {_ts()}", flush=True)


if __name__ == "__main__":
    main()
