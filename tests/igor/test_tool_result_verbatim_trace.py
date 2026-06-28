"""
test_tool_result_verbatim_trace.py — T-tool-result-verbatim-trace.

Tests the third truncation bug from the 2026-04-12/13 session: tool results
were capped at 2KB before the synth LLM ever saw them, so Igor structurally
could not read any file > 2KB end-to-end.

The fix is two-layered, mirroring the f0ad6dab user-input pattern:

  1. Raise the in-prompt synth window from 2KB to 40KB (modern context
     windows handle this trivially)
  2. Push the FULL untruncated tool result to TWM at category=
     'tool_result_verbatim' on dispatch — same gist+verbatim split

This module tests behavior #2 directly via a live cortex round-trip.
The synth window raise (#1) is verified by the source code grep test.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Verbatim TWM round-trip ──────────────────────────────────────────────────


def test_tool_result_verbatim_round_trip_via_twm_category():
    """Pushing a tool_result_verbatim observation to TWM and reading it
    back by category returns the full untruncated content. This is the
    same infrastructure pattern as user_input_verbatim from f0ad6dab."""
    import psycopg2
    from unseen_university.devices.igor.memory.cortex import Cortex

    db_url = os.environ.get(
        "UU_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    # Clean any prior test entries
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM twm_observations WHERE thread_id = %s AND category = %s",
        ("test:tool_verbatim_fidelity", "tool_result_verbatim"),
    )
    conn.close()

    # Realistic long tool result — 51KB-ish, like the file Akien tried
    # to read on 2026-04-13. Use repeated sentences (not 'x' * N) so the
    # credential scrubber doesn't redact it.
    sentence = (
        "The biomimicry framing keeps clicking — every fix today has been "
        "dumber and more correct than the framing it replaced. "
    )
    long_result = (sentence * 400)[:50000]
    assert len(long_result) > 40000, "test input must exceed the synth window"

    cortex = Cortex(None)
    cortex.twm_push(
        source="tool_result_verbatim",
        content_csb=long_result,
        salience=0.85,
        urgency=0.85,
        ttl_seconds=1800,
        category="tool_result_verbatim",
        thread_id="test:tool_verbatim_fidelity",
        metadata={
            "tool_name": "read_file",
            "turn_id": "tooltest1",
            "char_len": len(long_result),
        },
    )

    obs = cortex.twm_read(
        limit=10,
        include_integrated=True,
        thread_id="test:tool_verbatim_fidelity",
        category="tool_result_verbatim",
    )
    assert obs, "tool_result_verbatim observation not retrievable by category"
    latest = obs[-1]
    # Full content preserved — no truncation
    assert latest["content_csb"] == long_result, (
        f"verbatim was truncated: stored {len(latest['content_csb'])} chars "
        f"vs {len(long_result)} expected"
    )
    assert latest["metadata"].get("tool_name") == "read_file"
    assert latest["metadata"].get("char_len") == len(long_result)

    # Cleanup
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM twm_observations WHERE thread_id = %s AND category = %s",
        ("test:tool_verbatim_fidelity", "tool_result_verbatim"),
    )
    conn.close()


# ── Synth window cap raised — source-level verification ─────────────────────


def test_synth_window_raised_to_40k():
    """The synthesis prompt should slice tool results at 40KB, not 2KB.

    Source-level verification — the constant lives in main.py at the
    synth_prompt assembly site. A behavioral integration test would
    require running the full LLM tool dispatch path, which is heavy and
    LLM-dependent. The source check captures the actual fix.
    """
    main_py = Path(__file__).resolve().parent.parent.parent / "devices" / "igor" / "main.py"
    text = main_py.read_text()
    # The synth prompt block is uniquely identifiable by 'Result:\n' followed
    # by the truncation pattern.
    assert (
        "str(_tool_result)[:40000]" in text
    ), "synth prompt should slice tool result at 40000 chars (was 2000)"
    # And the legacy 2KB cap should not be present at the synth site
    assert (
        "str(_tool_result)[:2000]" not in text
    ), "legacy 2KB synth cap still present somewhere — should have been raised"


def test_fallback_window_raised_to_4k():
    """The user-visible fallback wrapper '[<tool> result: ...]' should
    slice at 4000 chars, not 800."""
    main_py = Path(__file__).resolve().parent.parent.parent / "devices" / "igor" / "main.py"
    text = main_py.read_text()
    assert (
        "str(_tool_result)[:4000]" in text
    ), "fallback wrapper should slice at 4000 chars (was 800)"
    # The legacy 800-char fallback should be gone
    assert (
        "str(_tool_result)[:800]" not in text
    ), "legacy 800-char fallback still present — should have been raised"


def test_tool_result_verbatim_push_in_main_py():
    """The dispatch path should push a tool_result_verbatim observation
    to TWM right after _tool_result is captured."""
    main_py = Path(__file__).resolve().parent.parent.parent / "devices" / "igor" / "main.py"
    text = main_py.read_text()
    assert 'source="tool_result_verbatim"' in text
    assert 'category="tool_result_verbatim"' in text
    # And it should reference the full result string, not a slice
    assert "_full_result_str" in text


# ── Ring entries stay short (correct — they're for compaction) ──────────────


def test_ring_truncations_unchanged_at_300_500():
    """Ring entries (RESOLVED, TOOL_RESULT) should stay short — they're
    for memory compaction, not for user-visible output. Verify the
    hotfix didn't accidentally inflate them too."""
    main_py = Path(__file__).resolve().parent.parent.parent / "devices" / "igor" / "main.py"
    text = main_py.read_text()
    # RESOLVED ring entries stay at 300
    assert "str(_tool_result)[:300]" in text
    # TOOL_RESULT ring entries stay at 500
    assert "str(_tool_result)[:500]" in text
