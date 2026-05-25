"""test_palace_metric.py — T-approach-frame-sensor-node.

Tests for wild_igor/igor/tools/palace_metric.py:
  - render_sparkline (pure function)
  - parse_history (pure function)
  - increment_metric + append_history round-trip (DB-backed)
  - render_history_sparkline end-to-end (DB-backed)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.tools import palace_metric

TEST_ROOT = "_test/palace_metric"
TEST_COUNTER = f"{TEST_ROOT}/counter"
TEST_HISTORY = f"{TEST_ROOT}/history"


@pytest.fixture
def palace_cur():
    """Open a palace DB connection, seed test nodes, yield cursor, cleanup."""
    db_url = os.environ.get("IGOR_HOME_DB_URL")
    if not db_url:
        pytest.skip("IGOR_HOME_DB_URL not set")
    import psycopg2

    conn = psycopg2.connect(db_url)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SET search_path TO clan, public")

    def _seed(path, parent, title, content):
        cur.execute(
            """
            INSERT INTO memory_palace (path, parent_path, title, content, pointers, updated_at, updated_by)
            VALUES (%s, %s, %s, %s, '[]'::jsonb, '2026-04-21', 'test_palace_metric')
            ON CONFLICT (path) DO UPDATE
            SET content = EXCLUDED.content,
                updated_at = EXCLUDED.updated_at
            """,
            (path, parent, title, content),
        )

    _seed(TEST_ROOT, None, "Test root for palace_metric", "test fixture")
    _seed(TEST_COUNTER, TEST_ROOT, "Test counter", "0")
    _seed(TEST_HISTORY, TEST_ROOT, "Test history", "")

    yield cur

    cur.execute("DELETE FROM memory_palace WHERE path LIKE %s", (f"{TEST_ROOT}%",))
    conn.close()


def test_render_sparkline_three_synthetic_rows():
    """3 synthetic values render to a 3-char sparkline."""
    out = palace_metric.render_sparkline([1, 5, 10])
    assert len(out) == 3
    assert out[0] != out[2]  # low vs high → different chars
    assert out[0] == " "  # vmin
    assert out[2] == "█"  # vmax


def test_render_sparkline_flat():
    out = palace_metric.render_sparkline([7, 7, 7, 7])
    assert out == "▄▄▄▄"  # flat line = middle glyph


def test_render_sparkline_empty():
    assert palace_metric.render_sparkline([]) == ""


def test_render_sparkline_tail_truncation():
    out = palace_metric.render_sparkline(list(range(100)), width=5)
    assert len(out) == 5


def test_parse_history_extracts_key_values():
    content = (
        "2026-04-21 12:00 | reviewed:5 reframed:2\n"
        "2026-04-21 13:00 | reviewed:10 reframed:6\n"
        "2026-04-21 14:00 | reviewed:15 reframed:9\n"
    )
    assert palace_metric.parse_history(content, "reviewed") == [5, 10, 15]
    assert palace_metric.parse_history(content, "reframed") == [2, 6, 9]
    assert palace_metric.parse_history(content, "nonexistent") == []


def test_parse_history_ignores_malformed_rows():
    content = (
        "2026-04-21 12:00 | reviewed:5\n"
        "garbage line without timestamp\n"
        "2026-04-21 13:00 | reviewed:bad\n"
        "2026-04-21 14:00 | reviewed:10\n"
    )
    assert palace_metric.parse_history(content, "reviewed") == [5, 10]


def test_increment_and_history_round_trip(palace_cur):
    """Simulate a batch-of-5 audit run: counter increments, history appends."""
    cur = palace_cur

    assert palace_metric.read_counter(cur, TEST_COUNTER) == 0

    for batch_size in (5, 5, 5, 5, 5):
        new_count = palace_metric.increment_metric(cur, TEST_COUNTER, by=batch_size)
        palace_metric.append_history(cur, TEST_HISTORY, f"reviewed:{new_count}")

    assert palace_metric.read_counter(cur, TEST_COUNTER) == 25

    cur.execute("SELECT content FROM memory_palace WHERE path = %s", (TEST_HISTORY,))
    history_content = cur.fetchone()[0]
    assert history_content.count("\n") == 5
    values = palace_metric.parse_history(history_content, "reviewed")
    assert values == [5, 10, 15, 20, 25]


def test_render_history_sparkline_end_to_end(palace_cur):
    cur = palace_cur
    for i in (3, 1, 4, 1, 5, 9, 2, 6):
        palace_metric.append_history(cur, TEST_HISTORY, f"reviewed:{i}")

    spark = palace_metric.render_history_sparkline(cur, TEST_HISTORY, "reviewed")
    assert len(spark) == 8
    assert spark[5] == "█"  # 9 is max
    assert spark[1] == " "  # 1 is min


def test_append_history_uses_explicit_timestamp(palace_cur):
    cur = palace_cur
    ts = datetime(2026, 4, 21, 17, 30)
    row = palace_metric.append_history(cur, TEST_HISTORY, "reviewed:7", ts=ts)
    assert row.startswith("2026-04-21 17:30 |")
