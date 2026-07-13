"""D-build-queue-filesystem-first-2026-06-19: the four targeted status mutators
(set_status_in_progress, reset_stale_in_progress, cmd_dispatch, cmd_next) route
through the filesystem ticket_store, which is the SOLE authority.

Postgres has been dropped from the ticket path entirely (T-ticket-pg-drop): there
is no DB mirror and no DB to be "down". These tests assert the mutators operate
correctly against the filesystem store alone, preserving the atomic
precondition-gated race-safety the old WHERE-gated PG UPDATE provided.
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


def _mk(tid, status="sprint", worker=None, **kw):
    # `intention` is REQUIRED for sprint entry (T-sprint-tickets-with-no-intention-
    # cannot-be-proven). Default it here so these mutator fixtures represent a
    # VALID post-gate ticket; the no-intention divert is pinned separately below.
    body = {"id": tid, "title": f"t {tid}", "status": status, "worker": worker,
            "priority": 0.5, "created_by": "cc.0",
            "intention": "I intend that the mutators route through ticket_store."}
    body.update(kw)
    return body


# ── set_status_in_progress ────────────────────────────────────────────────────


def test_set_in_progress_fs():
    ts.write(_mk("T-a", status="sprint"))
    assert cc_queue.set_status_in_progress("T-a") is True
    assert ts.read("T-a")["status"] == "in_progress"


def test_set_in_progress_precondition_blocks():
    ts.write(_mk("T-b", status="in_progress"))
    assert cc_queue.set_status_in_progress("T-b") is False   # not 'sprint'


def test_set_in_progress_missing_is_false():
    assert cc_queue.set_status_in_progress("T-none") is False


# ── reset_stale_in_progress ───────────────────────────────────────────────────


def test_reset_stale_fs():
    ts.write(_mk("T-c", status="in_progress", dispatched_at="2026-06-19T00:00:00Z"))
    assert cc_queue.reset_stale_in_progress("T-c") is True
    got = ts.read("T-c")
    assert got["status"] == "sprint"
    assert got["dispatched_at"] is None     # stale stamp cleared


def test_reset_stale_precondition_blocks():
    ts.write(_mk("T-d", status="sprint"))
    assert cc_queue.reset_stale_in_progress("T-d") is False


# ── cmd_dispatch ──────────────────────────────────────────────────────────────


def test_cmd_dispatch_fs(capsys):
    ts.write(_mk("T-e", status="sprint"))
    cc_queue.cmd_dispatch(["T-e", "--by", "granny"])
    got = ts.read("T-e")
    assert got["status"] == "in_progress"
    assert got["dispatched_by"] == "granny"
    assert got["dispatched_at"]


def test_cmd_dispatch_wrong_status_exits():
    ts.write(_mk("T-f", status="closed"))
    with pytest.raises(SystemExit):
        cc_queue.cmd_dispatch(["T-f"])


def test_cmd_dispatch_missing_exits():
    with pytest.raises(SystemExit):
        cc_queue.cmd_dispatch(["T-ghost"])


# ── cmd_next ──────────────────────────────────────────────────────────────────


def test_cmd_next_claims_fs(capsys):
    ts.write(_mk("T-g", status="sprint", worker="ds.0"))
    cc_queue.cmd_next(["--worker", "ds.0"])
    out = capsys.readouterr().out.strip()
    assert out == "T-g"
    assert ts.read("T-g")["status"] == "in_progress"


def test_cmd_next_empty_when_none(capsys):
    cc_queue.cmd_next(["--worker", "ds.0"])
    assert capsys.readouterr().out.strip() == ""


# ── reset_stale_in_progress honours the sprint-ENTRY gate ─────────────────────


def test_reset_stale_diverts_an_intentionless_ticket_to_triage():
    """A stale ticket with no intention is reset to the DESIGN step, not to sprint.

    It must never be STRANDED in `in_progress` — refusing the reset outright would
    pin it there forever with no way back, which is why the automatic paths divert
    rather than refuse. The gate holds the line without creating a dead end.
    """
    ts.write(_mk("T-noint", status="in_progress", intention=None))
    assert cc_queue.reset_stale_in_progress("T-noint") is True
    got = ts.read("T-noint")
    assert got["status"] == "triage", "no intention -> design step, not sprint"
    assert got["status"] != "in_progress", "and never left stranded"
