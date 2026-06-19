"""Slice (d) of D-build-queue-filesystem-first-2026-06-19: the four targeted
status mutators (set_status_in_progress, reset_stale_in_progress, cmd_dispatch,
cmd_next) route through the filesystem ticket_store (authoritative), with Postgres
demoted to a transitional loud, non-fatal mirror.

The load-bearing test: **with Postgres DOWN, the mutators still succeed** — that is
the entire point of the cutover. PG down is simulated by patching _db_conn to raise.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "devlab" / "claudecode"))

import cc_queue  # noqa: E402
from unseen_university import ticket_store as ts  # noqa: E402


@pytest.fixture(autouse=True)
def _fs(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setenv("UU_QUEUE_TRACE_DIR", str(tmp_path / "trace"))
    (tmp_path / "tickets").mkdir(parents=True, exist_ok=True)
    # classifier side effects import a device — neutralize for unit isolation.
    monkeypatch.setattr(cc_queue, "_classifier_stamp_in_flight", lambda *a, **k: None)
    yield tmp_path


def _pg_down(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("Postgres is down (simulated)")
    monkeypatch.setattr(cc_queue, "_db_conn", _boom)


def _mk(tid, status="sprint", worker=None, **kw):
    body = {"id": tid, "title": f"t {tid}", "status": status, "worker": worker,
            "priority": 0.5, "created_by": "cc.0"}
    body.update(kw)
    return body


# ── set_status_in_progress ────────────────────────────────────────────────────


def test_set_in_progress_fs_first_pg_down(monkeypatch):
    ts.write(_mk("T-a", status="sprint"))
    _pg_down(monkeypatch)
    assert cc_queue.set_status_in_progress("T-a") is True   # FS authoritative
    assert ts.read("T-a")["status"] == "in_progress"


def test_set_in_progress_precondition_blocks(monkeypatch):
    ts.write(_mk("T-b", status="in_progress"))
    _pg_down(monkeypatch)
    assert cc_queue.set_status_in_progress("T-b") is False   # not 'sprint'


def test_set_in_progress_missing_is_false(monkeypatch):
    _pg_down(monkeypatch)
    assert cc_queue.set_status_in_progress("T-none") is False


# ── reset_stale_in_progress ───────────────────────────────────────────────────


def test_reset_stale_fs_first_pg_down(monkeypatch):
    ts.write(_mk("T-c", status="in_progress", dispatched_at="2026-06-19T00:00:00Z"))
    _pg_down(monkeypatch)
    assert cc_queue.reset_stale_in_progress("T-c") is True
    got = ts.read("T-c")
    assert got["status"] == "sprint"
    assert got["dispatched_at"] is None     # stale stamp cleared


def test_reset_stale_precondition_blocks(monkeypatch):
    ts.write(_mk("T-d", status="sprint"))
    _pg_down(monkeypatch)
    assert cc_queue.reset_stale_in_progress("T-d") is False


# ── cmd_dispatch ──────────────────────────────────────────────────────────────


def test_cmd_dispatch_fs_first_pg_down(monkeypatch, capsys):
    ts.write(_mk("T-e", status="sprint"))
    _pg_down(monkeypatch)
    cc_queue.cmd_dispatch(["T-e", "--by", "granny"])
    got = ts.read("T-e")
    assert got["status"] == "in_progress"
    assert got["dispatched_by"] == "granny"
    assert got["dispatched_at"]


def test_cmd_dispatch_wrong_status_exits(monkeypatch):
    ts.write(_mk("T-f", status="closed"))
    _pg_down(monkeypatch)
    with pytest.raises(SystemExit):
        cc_queue.cmd_dispatch(["T-f"])


def test_cmd_dispatch_missing_exits(monkeypatch):
    _pg_down(monkeypatch)
    with pytest.raises(SystemExit):
        cc_queue.cmd_dispatch(["T-ghost"])


# ── cmd_next ──────────────────────────────────────────────────────────────────


def test_cmd_next_claims_fs_first_pg_down(monkeypatch, capsys):
    ts.write(_mk("T-g", status="sprint", worker="ds.0"))
    _pg_down(monkeypatch)
    cc_queue.cmd_next(["--worker", "ds.0"])
    out = capsys.readouterr().out.strip()
    assert out == "T-g"
    assert ts.read("T-g")["status"] == "in_progress"


def test_cmd_next_empty_when_none(monkeypatch, capsys):
    _pg_down(monkeypatch)
    cc_queue.cmd_next(["--worker", "ds.0"])
    assert capsys.readouterr().out.strip() == ""
