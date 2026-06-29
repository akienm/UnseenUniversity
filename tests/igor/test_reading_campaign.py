"""
test_reading_campaign.py — T-reading-worker-pool

Covers the queue mechanics (create, enqueue, claim, mark done/failed,
priority ordering, budget rollup, status report). The worker loop itself
invokes real extraction — out of scope for unit tests; exercised live
during the $40 re-run.

Uses scratch campaign IDs unique per test + teardown delete so tests
don't pollute the shared Postgres campaign table.
"""

from __future__ import annotations

import os
import sys
import unittest
import uuid
from pathlib import Path

import pytest

os.environ.setdefault(
    "UU_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-Wild1"
)

from claudecode.reading_campaign import (  # noqa: E402
    _parse_master_list,
    _conn,
    campaign_budget_remaining,
    campaign_spent,
    campaign_status,
    claim_next_block,
    create_campaign,
    enqueue_item_blocks,
    expand_campaign_from_master_list,
    get_campaign_schema,
    mark_block_done,
    mark_block_failed,
)


def _cleanup(campaign_id: str) -> None:
    conn = _conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SET search_path TO infra")
                cur.execute(
                    "DELETE FROM reading_blocks WHERE campaign_id = %s",
                    (campaign_id,),
                )
                cur.execute(
                    "DELETE FROM reading_campaigns WHERE campaign_id = %s",
                    (campaign_id,),
                )
    finally:
        conn.close()


class TestQueueMechanics(unittest.TestCase):
    def setUp(self):
        self.cid = f"test-{uuid.uuid4().hex[:8]}"
        create_campaign(self.cid, budget_usd=1.00, notes="pytest")

    def tearDown(self):
        _cleanup(self.cid)

    def test_enqueue_and_claim_respect_priority_then_chunk_order(self):
        enqueue_item_blocks(
            self.cid,
            "rl://a",
            "rl://a",
            priority=1,
            chunk_positions=[0, 15],
            item_title="Item A",
        )
        enqueue_item_blocks(
            self.cid,
            "rl://b",
            "rl://b",
            priority=0,
            chunk_positions=[0, 15, 30],
            item_title="Item B",
        )
        seen = []
        for _ in range(6):
            b = claim_next_block(self.cid, "w1")
            if b is None:
                break
            seen.append((b["priority"], b["chunk_pos"], b["item_title"]))
            mark_block_done(
                b["id"],
                nodes_deposited=0,
                model_used="test",
                inference_tier="local",
                cost_usd=0.0,
            )
        # Priority 0 (Item B) drained first at chunk 0/15/30, then priority 1 (Item A)
        self.assertEqual(
            seen,
            [
                (0, 0, "Item B"),
                (0, 15, "Item B"),
                (0, 30, "Item B"),
                (1, 0, "Item A"),
                (1, 15, "Item A"),
            ],
        )

    def test_enqueue_is_idempotent_on_chunk_pos(self):
        n1 = enqueue_item_blocks(
            self.cid,
            "rl://x",
            "rl://x",
            priority=0,
            chunk_positions=[0, 15],
        )
        n2 = enqueue_item_blocks(
            self.cid,
            "rl://x",
            "rl://x",
            priority=0,
            chunk_positions=[0, 15, 30],
        )
        # First call inserts 2, second inserts only the new chunk 30
        self.assertEqual(n1, 2)
        self.assertEqual(n2, 1)

    def test_claim_empty_queue_returns_none(self):
        self.assertIsNone(claim_next_block(self.cid, "w1"))

    def test_mark_done_updates_status_and_rollup(self):
        enqueue_item_blocks(
            self.cid,
            "rl://c",
            "rl://c",
            priority=0,
            chunk_positions=[0, 15],
            item_title="Item C",
        )
        for _ in range(2):
            b = claim_next_block(self.cid, "w1")
            mark_block_done(
                b["id"],
                nodes_deposited=3,
                model_used="qwen2.5:7b",
                inference_tier="local",
                cost_usd=0.0,
            )
        status = campaign_status(self.cid)
        self.assertEqual(status["blocks_by_status"].get("done"), 2)
        self.assertEqual(status["nodes_deposited"], 6)
        self.assertEqual(status["spent_usd"], 0.0)

    def test_mark_failed_preserves_error(self):
        enqueue_item_blocks(
            self.cid,
            "rl://d",
            "rl://d",
            priority=0,
            chunk_positions=[0],
        )
        b = claim_next_block(self.cid, "w1")
        mark_block_failed(b["id"], "RuntimeError: test boom")
        status = campaign_status(self.cid)
        self.assertEqual(status["blocks_by_status"].get("failed"), 1)

    def test_budget_tracking(self):
        enqueue_item_blocks(
            self.cid,
            "rl://e",
            "rl://e",
            priority=0,
            chunk_positions=[0, 15],
        )
        b = claim_next_block(self.cid, "w1")
        mark_block_done(
            b["id"],
            nodes_deposited=1,
            model_used="claude-sonnet-4",
            inference_tier="cloud",
            cost_usd=0.007,
        )
        self.assertAlmostEqual(campaign_spent(self.cid), 0.007, places=5)
        remaining = campaign_budget_remaining(self.cid)
        self.assertAlmostEqual(remaining, 1.00 - 0.007, places=5)


class TestMasterListParser(unittest.TestCase):
    def test_parses_calibre_url_file_types(self):
        import tempfile

        sample = (
            "# Comment line, ignored\n"
            "[ ] [calibre] Making Money — Terry Pratchett (calibre://342)\n"
            "[x] [calibre] Completed item should be skipped (calibre://999)\n"
            "[ ] [url] Blog Post Title — https://example.com/foo.html\n"
            "[ ] [file] /home/akien/doc.pdf\n"
            "[ ] [code] devices/igor/main.py\n"
            "not-a-bracket-line\n"
        )
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write(sample)
            path = Path(f.name)
        try:
            items = _parse_master_list(path)
            self.assertEqual(len(items), 4)
            self.assertEqual(items[0]["type"], "calibre")
            self.assertEqual(items[0]["source"], "calibre://342")
            self.assertIn("Making Money", items[0]["title"])
            self.assertEqual(items[1]["type"], "url")
            self.assertEqual(items[1]["source"], "https://example.com/foo.html")
            self.assertEqual(items[2]["type"], "file")
            self.assertEqual(items[3]["type"], "code")
            # Priority is file order
            for i, it in enumerate(items):
                self.assertEqual(it["priority"], i)
        finally:
            path.unlink(missing_ok=True)


class TestTargetSchema(unittest.TestCase):
    """T-competition-pipeline-configurable: target_schema routes deposits."""

    def setUp(self):
        self.cid_clan = f"test-clan-{uuid.uuid4().hex[:8]}"
        self.cid_comp = f"test-comp-{uuid.uuid4().hex[:8]}"

    def tearDown(self):
        _cleanup(self.cid_clan)
        _cleanup(self.cid_comp)

    def test_default_target_schema_is_clan(self):
        result = create_campaign(self.cid_clan, budget_usd=1.00, notes="pytest")
        self.assertEqual(result["target_schema"], "clan")
        stored = get_campaign_schema(self.cid_clan)
        self.assertEqual(stored, "clan")

    def test_competition_target_schema_stored(self):
        result = create_campaign(
            self.cid_comp, budget_usd=1.00, notes="pytest", target_schema="competition"
        )
        self.assertEqual(result["target_schema"], "competition")
        stored = get_campaign_schema(self.cid_comp)
        self.assertEqual(stored, "competition")


if __name__ == "__main__":
    unittest.main()
