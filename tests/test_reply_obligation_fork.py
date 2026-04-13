"""
test_reply_obligation_fork.py — T-reply-obligation-fork unit tests.

Five tests covering the biomimicry-framed reply-obligation pipeline:

  1. goal_adopt records origin context when awaiting_reply=True
  2. goal_adopt backward-compatible (no origin kwargs → no origin metadata)
  3. submit_background propagates goal_id into the completion queue item
  4. PROC_REPLY_OBLIGATION_LOOK habit row exists with the right metadata
  5. The seed script is idempotent

The completion-drain bouquet (origin question / goal refresh / pending_reply
marker pushes) is verified by an end-to-end probe rather than a unit test —
TWM competition is the whole point and asserting on intermediate state would
re-couple the test to a mechanism we deliberately decoupled.
"""

import sys
from collections import deque
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── 1. goal_adopt records origin context ─────────────────────────────────────


def test_goal_adopt_records_origin_when_awaiting_reply():
    """goal_adopt with awaiting_reply=True stores origin thread/turn/question
    and pushes the goal to TWM at salience 0.90 (vs default 0.85)."""
    from wild_igor.igor.tools import ops as _ops

    captured_mems = []
    captured_pushes = []

    class _FakeCortex:
        def __init__(self, _):
            pass

        def store(self, mem):
            captured_mems.append(mem)

        def twm_push(self, **kw):
            captured_pushes.append(kw)
            return 1

    with patch.object(_ops, "datetime") as _dt:
        _dt.now.return_value.strftime.side_effect = lambda fmt: (
            "20260412180000123456" if "f" in fmt else "18:00"
        )
        _dt.now.return_value.isoformat.return_value = "2026-04-12T18:00:00"
        with patch("wild_igor.igor.memory.cortex.Cortex", _FakeCortex):
            result = _ops.goal_adopt(
                "look at the ticket list",
                goal_id="GOAL_TEST_001",
                origin_thread_id="web:shared",
                origin_turn_id="abcd1234",
                origin_question="what's on the slate today?",
                awaiting_reply=True,
            )

    assert "Goal set" in result
    assert len(captured_mems) == 1
    mem = captured_mems[0]
    assert mem.id == "GOAL_TEST_001"
    assert mem.metadata["awaiting_reply"] is True
    assert mem.metadata["origin_thread_id"] == "web:shared"
    assert mem.metadata["origin_turn_id"] == "abcd1234"
    assert mem.metadata["origin_question"] == "what's on the slate today?"

    assert len(captured_pushes) == 1
    push = captured_pushes[0]
    assert push["salience"] == 0.90
    assert push["thread_id"] == "web:shared"
    assert push["category"] == "active_goal"


# ── 2. goal_adopt is backward-compatible ─────────────────────────────────────


def test_goal_adopt_no_origin_when_not_awaiting_reply():
    """Default callers (no awaiting_reply) get the original 0.85-salience
    behavior with no origin_* metadata fields."""
    from wild_igor.igor.tools import ops as _ops

    captured_mems = []
    captured_pushes = []

    class _FakeCortex:
        def __init__(self, _):
            pass

        def store(self, mem):
            captured_mems.append(mem)

        def twm_push(self, **kw):
            captured_pushes.append(kw)
            return 1

    with patch("wild_igor.igor.memory.cortex.Cortex", _FakeCortex):
        result = _ops.goal_adopt("write the failing test")

    assert "Goal set" in result
    mem = captured_mems[0]
    assert "awaiting_reply" not in mem.metadata
    assert "origin_thread_id" not in mem.metadata
    assert "origin_turn_id" not in mem.metadata
    assert "origin_question" not in mem.metadata
    assert captured_pushes[0]["salience"] == 0.85


# ── 3. submit_background propagates goal_id ──────────────────────────────────


def test_submit_background_propagates_goal_id_into_completion_item():
    """The completion item dict carries goal_id so the drain can find the
    obligation goal back."""
    from wild_igor.igor.cognition.job_manager import JobManager

    jm = JobManager()
    completions: deque = deque()

    def _fn():
        return "the answer"

    job_id = jm.submit_background(
        fn=_fn,
        title="fork:look at ticket list",
        completions_queue=completions,
        thread_id="web:shared",
        goal_id="GOAL_TEST_002",
    )

    # Daemon thread runs immediately; poll briefly for completion.
    import time

    deadline = time.time() + 2.0
    while not completions and time.time() < deadline:
        time.sleep(0.01)

    assert completions, "background job did not complete in time"
    item = completions[0]
    assert item["job_id"] == job_id
    assert item["goal_id"] == "GOAL_TEST_002"
    assert item["thread_id"] == "web:shared"
    assert item["result"] == "the answer"


def test_submit_background_default_goal_id_is_none():
    """Existing callers that don't pass goal_id get None — no breakage."""
    from wild_igor.igor.cognition.job_manager import JobManager

    jm = JobManager()
    completions: deque = deque()

    jm.submit_background(
        fn=lambda: "x",
        title="fork:legacy",
        completions_queue=completions,
        thread_id="cc:shared",
    )

    import time

    deadline = time.time() + 2.0
    while not completions and time.time() < deadline:
        time.sleep(0.01)

    assert completions
    assert completions[0]["goal_id"] is None


# ── 4. PROC_REPLY_OBLIGATION_LOOK row exists with right metadata ─────────────


def test_reply_obligation_habit_seeded():
    """The seed script populated PROC_REPLY_OBLIGATION_LOOK with awaiting_reply,
    fork_bg, conversation_eligible, intent gating, and broadened triggers."""
    import psycopg2
    import os
    import json

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute(
        "SELECT metadata::text FROM memories WHERE id = 'PROC_REPLY_OBLIGATION_LOOK'"
    )
    row = cur.fetchone()
    conn.close()

    assert row is not None, "PROC_REPLY_OBLIGATION_LOOK not seeded"
    meta = json.loads(row[0])
    assert meta.get("awaiting_reply") is True
    assert meta.get("fork_bg") is True
    assert meta.get("conversation_eligible") is True
    assert meta.get("conditions", {}).get("intent") == ["conversation", "general"]

    trigger = meta.get("trigger", "")
    # The narrow phrase set we committed to in the plan
    for phrase in (
        "let me look at",
        "let me check",
        "thinking about that",
        "i'll check",
    ):
        assert phrase in trigger, f"missing trigger phrase: {phrase}"


# ── 5. Seed script is idempotent ─────────────────────────────────────────────


def test_seed_script_is_idempotent():
    """Running the seed twice doesn't error and doesn't create a duplicate row."""
    from wild_igor.igor.tools import seed_reply_obligation_look as _seed
    import psycopg2
    import os

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )

    rc1 = _seed.seed()
    rc2 = _seed.seed()
    assert rc1 == 0 and rc2 == 0

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM memories WHERE id = 'PROC_REPLY_OBLIGATION_LOOK'")
    count = cur.fetchone()[0]
    conn.close()
    assert count == 1
