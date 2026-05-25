"""
test_instance_tracker.py — T-instance-tracking-startup (#424)

Unit tests for instance boot/shutdown recording. MagicMocked cortex so we
don't need a live Postgres — the point is to verify:

  1. record_startup writes BOTH a JSONL line AND calls DB insert
  2. The structured status format is yyyymmdd.hhmmssuuuuuu.xxxxxxx
  3. record_shutdown writes a shutdown record the same way
  4. Neither path pushes anything to TWM (verified by inspecting the
     MagicMock cortex — twm_push is never called)
  5. igor_instance_current returns the formatted string for the most
     recent boot row
  6. igor_instance_history orders by recency and accepts limit
  7. Graceful degradation when cortex is None or DB errors
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from devices.igor.tools import instance_tracker as it  # noqa: E402

# ── helpers ──────────────────────────────────────────────────────────────────


def _make_mock_cortex(rows=None):
    """Return a MagicMock cortex whose _db() context yields a cursor-like conn.

    rows: list of tuples to return from fetchall(), or a single tuple for fetchone().
    """
    cortex = MagicMock()
    conn = MagicMock()
    cortex._db.return_value.__enter__.return_value = conn
    cortex._db.return_value.__exit__.return_value = False
    if rows is None:
        conn.fetchone.return_value = None
        conn.fetchall.return_value = []
    elif isinstance(rows, tuple):
        conn.fetchone.return_value = rows
        conn.fetchall.return_value = [rows]
    else:
        conn.fetchone.return_value = rows[0] if rows else None
        conn.fetchall.return_value = rows
    return cortex, conn


@pytest.fixture
def tmp_jsonl(tmp_path, monkeypatch):
    """Redirect the JSONL path to a tmp dir for the test."""
    target = tmp_path / "instance_log.jsonl"
    monkeypatch.setattr(it, "_jsonl_path", lambda: target)
    return target


# ── format_status ────────────────────────────────────────────────────────────


def test_format_status_shape():
    out = it._format_status("2026-04-14T11:00:00.123456", "abc1234")
    assert out == "20260414.110000123456.abc1234"


def test_format_status_pads_microseconds():
    out = it._format_status("2026-04-14T11:00:00.000042", "abc1234")
    # microseconds must always be 6 digits
    assert ".110000000042." in out


def test_format_status_handles_bad_timestamp():
    out = it._format_status("not-a-date", "abc1234")
    assert out.endswith(".abc1234")
    assert "????" in out


def test_format_status_handles_missing_commit():
    out = it._format_status("2026-04-14T11:00:00.123456", "")
    assert out.endswith(".unknown")


# ── record_startup ───────────────────────────────────────────────────────────


def test_record_startup_writes_jsonl(tmp_jsonl):
    cortex, conn = _make_mock_cortex()
    rec = it.record_startup(cortex, "Igor-test")
    assert tmp_jsonl.exists()
    line = tmp_jsonl.read_text().strip()
    parsed = json.loads(line)
    assert parsed["event"] == "boot"
    assert parsed["instance_id"] == "Igor-test"
    assert "timestamp" in parsed
    assert "pid" in parsed
    assert rec["event"] == "boot"


def test_record_startup_writes_db(tmp_jsonl):
    cortex, conn = _make_mock_cortex()
    it.record_startup(cortex, "Igor-test")
    # Verify execute was called with an INSERT into instance_log
    calls = conn.execute.call_args_list
    assert len(calls) == 1
    sql = calls[0][0][0]
    assert "INSERT INTO instance_log" in sql
    params = calls[0][0][1]
    # params: (timestamp, event, instance_id, commit_short, commit_long, branch, host, pid, narrative)
    assert params[1] == "boot"
    assert params[2] == "Igor-test"
    assert isinstance(params[7], int)  # pid


def test_record_startup_does_not_push_to_twm(tmp_jsonl):
    """Load-bearing: boot events MUST NOT be pushed to TWM. Reference state, not working memory."""
    cortex, conn = _make_mock_cortex()
    it.record_startup(cortex, "Igor-test")
    # MagicMock records all method calls; assert twm_push was never touched
    assert not cortex.twm_push.called
    assert not cortex.ring_push.called
    assert not cortex.store.called


def test_record_startup_survives_db_failure(tmp_jsonl):
    """JSONL must still be written even if the DB insert blows up."""
    cortex = MagicMock()
    cortex._db.side_effect = RuntimeError("db down")
    rec = it.record_startup(cortex, "Igor-test")
    assert rec is not None
    assert tmp_jsonl.exists()
    parsed = json.loads(tmp_jsonl.read_text().strip())
    assert parsed["event"] == "boot"


def test_record_startup_survives_jsonl_failure(tmp_path, monkeypatch):
    """DB must still be tried even if JSONL write blows up."""
    monkeypatch.setattr(it, "_jsonl_path", lambda: tmp_path / "nope" / "missing.jsonl")
    # Parent dir will be created by _write_jsonl; force the failure differently
    # by making the target directory unwritable — instead, point at /dev/null/foo
    monkeypatch.setattr(
        it, "_jsonl_path", lambda: Path("/dev/null/not/a/real/path.jsonl")
    )
    cortex, conn = _make_mock_cortex()
    it.record_startup(cortex, "Igor-test")
    assert conn.execute.called  # DB insert was still attempted


# ── record_shutdown ──────────────────────────────────────────────────────────


def test_record_shutdown_writes_both(tmp_jsonl):
    cortex, conn = _make_mock_cortex()
    it.record_shutdown(cortex, "Igor-test")
    assert tmp_jsonl.exists()
    parsed = json.loads(tmp_jsonl.read_text().strip())
    assert parsed["event"] == "shutdown"
    assert conn.execute.called


def test_record_shutdown_no_twm(tmp_jsonl):
    cortex, conn = _make_mock_cortex()
    it.record_shutdown(cortex, "Igor-test")
    assert not cortex.twm_push.called


# ── igor_instance_current ────────────────────────────────────────────────────


def test_instance_current_formats_most_recent_boot(tmp_jsonl):
    row = (
        "2026-04-14T11:00:00.123456",  # timestamp
        "boot",  # event
        "Igor-test",  # instance_id
        "abc1234",  # commit_short
        "abc1234deadbeef",  # commit_long
        "main",  # branch
        "host1",  # host
        12345,  # pid
        "boot",  # narrative
    )
    cortex, _ = _make_mock_cortex(rows=row)
    out = it.igor_instance_current(cortex=cortex)
    assert out == "20260414.110000123456.abc1234"


def test_instance_current_none_when_empty(tmp_jsonl):
    cortex, _ = _make_mock_cortex(rows=[])
    out = it.igor_instance_current(cortex=cortex)
    assert "no boot records" in out


def test_instance_current_handles_no_cortex():
    out = it.igor_instance_current(cortex=None)
    assert "not available" in out


def test_instance_current_handles_db_error():
    cortex = MagicMock()
    cortex._db.side_effect = RuntimeError("boom")
    out = it.igor_instance_current(cortex=cortex)
    assert "no boot records" in out  # graceful degradation via _most_recent_boot


# ── igor_instance_history ────────────────────────────────────────────────────


def test_instance_history_orders_newest_first(tmp_jsonl):
    rows = [
        ("2026-04-14T11:00:00.000000", "boot", "c2", "main", "h1", 200),
        ("2026-04-14T10:00:00.000000", "shutdown", "c1", "main", "h1", 100),
    ]
    cortex, _ = _make_mock_cortex(rows=rows)
    out = it.igor_instance_history(cortex=cortex, limit=5)
    lines = out.splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("boot")
    assert lines[1].startswith("shutdown")
    assert "pid=200" in lines[0]


def test_instance_history_empty(tmp_jsonl):
    cortex, _ = _make_mock_cortex(rows=[])
    out = it.igor_instance_history(cortex=cortex)
    assert "no records" in out


def test_instance_history_clamps_limit(tmp_jsonl):
    cortex, conn = _make_mock_cortex(rows=[])
    it.igor_instance_history(cortex=cortex, limit=9999)
    # Limit was clamped to 100 in the SQL call
    params = conn.execute.call_args[0][1]
    assert params == (100,)


def test_instance_history_handles_bad_limit(tmp_jsonl):
    cortex, conn = _make_mock_cortex(rows=[])
    it.igor_instance_history(cortex=cortex, limit="not-a-number")
    params = conn.execute.call_args[0][1]
    assert params == (10,)  # default


def test_instance_history_no_cortex():
    out = it.igor_instance_history(cortex=None)
    assert "not available" in out


# ── Tool registration ────────────────────────────────────────────────────────


def test_tools_registered():
    from lab.utility_closet.registry import registry

    assert "igor_instance_current" in registry._tools
    assert "igor_instance_history" in registry._tools
