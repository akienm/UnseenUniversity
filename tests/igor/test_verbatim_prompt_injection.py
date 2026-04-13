"""
test_verbatim_prompt_injection.py — T-verbatim-prompt-injection.

Tests that _build_verbatim_block now includes the most recent
tool_result_verbatim entry alongside the existing user-input verbatim
section, so Igor can actually reason about long tool outputs (file reads,
etc.) on the next turn.

Closes the loop on the 2026-04-12/13 read_file experiment:

  1. Bug 1 (tool-call parser flat-key) — fixed in f0ad6dab
  2. Bug 2 (cross-turn user-input gist truncation) — verbatim_source TWM
     trace + verbatim block in prompt — fixed in f0ad6dab
  3. Bug 3 (tool result 2KB synth cap + no consumer) — synth cap raised
     to 40K + tool_result_verbatim TWM trace pushed in 935f2d8b — but no
     code read it back. THIS ticket adds the consumer.

Tests cover:
  - block includes recent tool_result_verbatim when present
  - block omits tool section when no tool_result_verbatim entries match
  - per-entry char budget enforced; truncation marker shown
  - both user input and tool result sections coexist when both present
  - empty thread (no entries either kind) returns empty string
  - tool result section labels the source tool name
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _fresh_igor():
    """Construct an Igor with just enough for _build_verbatim_block.

    We bypass __init__ to avoid the heavy Igor boot path; instead we set
    only the attributes _build_verbatim_block actually reads. If the helper
    grows new attribute dependencies later, add them here.
    """
    from wild_igor.igor.main import Igor
    from wild_igor.igor.memory.cortex import Cortex

    inst = Igor.__new__(Igor)
    inst.cortex = Cortex(None)
    inst._THREAD_MAX_HISTORY = 8  # set in Igor.__init__ at line 747
    return inst


def _push_verbatim_source(thread_id: str, content: str):
    from wild_igor.igor.memory.cortex import Cortex

    Cortex(None).twm_push(
        source="user_input_verbatim",
        content_csb=content,
        salience=0.85,
        urgency=0.9,
        ttl_seconds=1800,
        category="verbatim_source",
        thread_id=thread_id,
        metadata={"author": "user", "char_len": len(content)},
    )


def _push_tool_result_verbatim(thread_id: str, content: str, tool: str = "read_file"):
    from wild_igor.igor.memory.cortex import Cortex

    Cortex(None).twm_push(
        source="tool_result_verbatim",
        content_csb=content,
        salience=0.85,
        urgency=0.85,
        ttl_seconds=1800,
        category="tool_result_verbatim",
        thread_id=thread_id,
        metadata={"tool_name": tool, "char_len": len(content)},
    )


def _clear(thread_id: str):
    """Wipe both verbatim categories for a thread between tests."""
    from wild_igor.igor.memory.cortex import Cortex
    import psycopg2
    import os

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM twm_observations WHERE thread_id = %s "
        "AND category IN ('verbatim_source', 'tool_result_verbatim')",
        (thread_id,),
    )
    conn.close()


@pytest.fixture(autouse=True)
def cleanup_each():
    yield
    _clear("test:vbt_inject")


# ── Tool result section ──────────────────────────────────────────────────────


def test_block_includes_tool_result_verbatim_when_present():
    igor = _fresh_igor()
    long_result = (
        "the file content goes here. " * 100
    )  # ~2700 chars, well under the 30K cap
    _push_tool_result_verbatim("test:vbt_inject", long_result, tool="read_file")

    block = igor._build_verbatim_block("test:vbt_inject")
    assert "Most recent tool result" in block
    assert "read_file" in block
    # Full content present (not truncated since under cap)
    assert long_result in block


def test_block_omits_tool_section_when_no_entries_match():
    igor = _fresh_igor()
    # No pushes of any kind
    block = igor._build_verbatim_block("test:vbt_inject")
    assert block == ""


def test_block_truncates_tool_result_above_cap():
    from wild_igor.igor.main import Igor

    igor = _fresh_igor()
    # Use repeated sentence (not raw chars) to dodge credential scrubber
    sentence = (
        "a long file with lots of meaningful text that needs to be processed "
        "by Igor's reasoning when he eventually has to talk about it. "
    )
    huge_result = (sentence * 600)[: Igor._TOOL_RESULT_INJECT_MAX_CHARS + 5000]
    assert len(huge_result) > Igor._TOOL_RESULT_INJECT_MAX_CHARS
    _push_tool_result_verbatim("test:vbt_inject", huge_result, tool="read_file")

    block = igor._build_verbatim_block("test:vbt_inject")
    assert "truncated at" in block
    assert "tool_result_verbatim" in block  # references the TWM category for retrieval
    # The injected portion stops at the cap (give a small margin for the marker text)
    truncation_idx = block.find("... [truncated")
    assert truncation_idx > 0
    injected_content = block[block.find("read_file") : truncation_idx]
    assert len(injected_content) <= Igor._TOOL_RESULT_INJECT_MAX_CHARS + 200


def test_block_uses_most_recent_tool_result_only():
    """When multiple tool_result_verbatim entries exist, only the most
    recent is injected. Older entries stay in TWM but don't auto-flood."""
    igor = _fresh_igor()
    _push_tool_result_verbatim("test:vbt_inject", "OLDER result", tool="read_file")
    _push_tool_result_verbatim("test:vbt_inject", "NEWER result", tool="read_file")

    block = igor._build_verbatim_block("test:vbt_inject")
    assert "NEWER result" in block
    assert "OLDER result" not in block


# ── Combined user + tool sections ────────────────────────────────────────────


def test_block_contains_both_user_and_tool_sections():
    igor = _fresh_igor()
    _push_verbatim_source(
        "test:vbt_inject",
        "Read this now: /home/akien/TheIgorsProject/akien/Readings/transcript.txt",
    )
    _push_tool_result_verbatim(
        "test:vbt_inject",
        "the transcript starts with akien:hello and continues for many lines",
        tool="read_file",
    )

    block = igor._build_verbatim_block("test:vbt_inject")
    assert "Recent user input" in block
    assert "Most recent tool result" in block
    # User input present
    assert "/home/akien/TheIgorsProject" in block
    # Tool result present
    assert "akien:hello" in block


def test_block_works_with_only_user_section():
    """Backward compatibility — when only user input verbatims exist (no
    tool result), the block still produces the user section."""
    igor = _fresh_igor()
    _push_verbatim_source("test:vbt_inject", "hello with /a/path/in/it")

    block = igor._build_verbatim_block("test:vbt_inject")
    assert "Recent user input" in block
    assert "/a/path/in/it" in block
    assert "Most recent tool result" not in block


def test_block_works_with_only_tool_section():
    """Symmetric case — tool result without user input."""
    igor = _fresh_igor()
    _push_tool_result_verbatim(
        "test:vbt_inject", "tool output content here", tool="list_directory"
    )

    block = igor._build_verbatim_block("test:vbt_inject")
    assert "Most recent tool result" in block
    assert "list_directory" in block
    assert "Recent user input" not in block


def test_tool_result_section_labels_source_tool():
    igor = _fresh_igor()
    _push_tool_result_verbatim(
        "test:vbt_inject", "content from a different tool", tool="read_system_file"
    )

    block = igor._build_verbatim_block("test:vbt_inject")
    assert "read_system_file" in block
