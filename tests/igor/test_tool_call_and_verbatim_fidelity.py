"""
test_tool_call_and_verbatim_fidelity.py — T-tool-call-and-verbatim-fidelity.

Four tests covering the two bugs fixed by this ticket:

Bug 1 — tool-call parser accepts flat-key JSON from LLMs that emit args as
top-level siblings of `name` instead of nested under `arguments`.

Bug 2 — cross-turn verbatim fidelity: user input ingestion seeds a parallel
verbatim TWM trace at category='verbatim_source', and the thread context
prefix builder includes that trace in a labeled section so later turns can
reach fidelity-critical content (paths, URLs, code) via salience competition
without reconstructing from the 200-char gist.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.main import Igor

# ── Bug 1: tool-call parser flat-key fallback ────────────────────────────────


def test_tool_call_parser_accepts_nested_arguments():
    """Standard OpenAI format with nested 'arguments' still works."""
    text = (
        "here is the call\n"
        "<tool_call>\n"
        '{"name": "read_file", "arguments": {"path": "/tmp/foo"}}\n'
        "</tool_call>\n"
        "thanks"
    )
    name, kwargs, cleaned = Igor._extract_tool_call(text)
    assert name == "read_file"
    assert kwargs == {"path": "/tmp/foo"}
    assert "<tool_call>" not in cleaned
    assert "here is the call" in cleaned


def test_tool_call_parser_accepts_flat_path_key():
    """Flat format where path is a top-level sibling of name also works.

    This is the exact shape that caused the 2026-04-12 read_file failure —
    some models emit {"name": "read_file", "path": "..."} instead of
    {"name": "read_file", "arguments": {"path": "..."}}.
    """
    text = (
        "<tool_call>\n"
        '{"name": "read_file", "path": "/home/akien/TheIgorsProject/akien/Readings/20260412.ClaudeBecameABiomimeticEngineer.txt"}\n'
        "</tool_call>"
    )
    name, kwargs, cleaned = Igor._extract_tool_call(text)
    assert name == "read_file"
    assert kwargs == {
        "path": "/home/akien/TheIgorsProject/akien/Readings/20260412.ClaudeBecameABiomimeticEngineer.txt"
    }


def test_tool_call_parser_accepts_flat_multiple_kwargs():
    """Flat format with multiple top-level sibling keys — every key other
    than 'name' becomes a kwarg."""
    text = (
        "<tool_call>\n"
        '{"name": "write_file", "path": "/tmp/x", "content": "hello"}\n'
        "</tool_call>"
    )
    name, kwargs, _cleaned = Igor._extract_tool_call(text)
    assert name == "write_file"
    assert kwargs == {"path": "/tmp/x", "content": "hello"}


def test_tool_call_parser_prefers_nested_over_flat():
    """If the payload has both a nested 'arguments' key AND top-level
    siblings, the nested form wins — that's the standard shape."""
    text = (
        "<tool_call>\n"
        '{"name": "read_file", "arguments": {"path": "/correct"}, "path": "/wrong"}\n'
        "</tool_call>"
    )
    name, kwargs, _cleaned = Igor._extract_tool_call(text)
    assert name == "read_file"
    assert kwargs == {"path": "/correct"}


# ── Bug 2: verbatim TWM trace ────────────────────────────────────────────────


def test_verbatim_trace_push_and_retrieve_via_twm_category():
    """Pushing a verbatim_source observation to TWM and reading it back by
    category returns the full untruncated content."""
    import psycopg2
    import os

    # Use a live cortex against the running Postgres — this verifies the
    # category-filtered twm_read path which is the load-bearing infrastructure.
    from devices.igor.memory.cortex import Cortex

    db_url = os.environ.get(
        "IGOR_HOME_DB_URL",
        "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001",
    )
    # Clean any prior test entries for a known sentinel thread
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM twm_observations WHERE thread_id = %s AND category = %s",
        ("test:verbatim_fidelity", "verbatim_source"),
    )
    conn.close()

    cortex = Cortex(None)
    long_input = (
        "so last time, you were having trouble with threading. so i asked "
        "claude to look at your head from the perspective of a biomimetic "
        "engineer. Read this now: /home/akien/TheIgorsProject/akien/Readings/"
        "20260412.ClaudeBecameABiomimeticEngineer.txt"
    )
    assert len(long_input) > 200, "test input must exceed the gist chop limit"

    cortex.twm_push(
        source="user_input_verbatim",
        content_csb=long_input,
        salience=0.85,
        urgency=0.9,
        ttl_seconds=1800,
        category="verbatim_source",
        thread_id="test:verbatim_fidelity",
        metadata={"turn_id": "test1234", "author": "user", "char_len": len(long_input)},
    )

    obs = cortex.twm_read(
        limit=50,
        include_integrated=True,
        thread_id="test:verbatim_fidelity",
        category="verbatim_source",
    )
    assert obs, "verbatim_source observation not retrievable by category"
    # Most recent matches our push
    latest = obs[-1]
    assert latest["content_csb"] == long_input, (
        "verbatim content was truncated or mangled — "
        f"stored {len(latest['content_csb'])} chars vs {len(long_input)} expected"
    )
    # Full path must be intact
    assert "20260412.ClaudeBecameABiomimeticEngineer.txt" in latest["content_csb"]

    # Cleanup
    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "DELETE FROM twm_observations WHERE thread_id = %s AND category = %s",
        ("test:verbatim_fidelity", "verbatim_source"),
    )
    conn.close()
