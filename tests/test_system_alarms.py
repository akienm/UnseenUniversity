"""Proof for T-system-alarms-primitive.

Exercises the completion criteria: same-signature drops dedup to one file with
correct aggregate + per-caller counts; close relocates to archive/; the
self-clear prunes quiet callers and ages out emptied alarms; raise_alarm emits a
log line; and a drop failure never raises into the caller.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from unseen_university import system_alarms as sa


@pytest.fixture(autouse=True)
def _redirect_home(tmp_path, monkeypatch):
    """Point IGOR_HOME at a tmp dir so alarms never touch the real store."""
    monkeypatch.setattr("unseen_university.system_alarms.uu_home", lambda: str(tmp_path))
    return tmp_path


def test_dedup_same_signature_one_file_with_counts():
    sa.raise_alarm("no-provider:worker", "caller.a", "down", emit_log=False)
    sa.raise_alarm("no-provider:worker", "caller.a", "down", emit_log=False)
    res = sa.raise_alarm("no-provider:worker", "caller.b", "down", emit_log=False)

    assert res.status == "incremented"
    open_alarms = sa.list_alarms()
    assert len(open_alarms) == 1, "same signature must dedup to ONE file"
    rec = open_alarms[0]
    assert rec["count"] == 3
    assert rec["callers"] == {"caller.a": 2, "caller.b": 1}  # the punch-list


def test_first_drop_is_new():
    res = sa.raise_alarm("specific-model:gpt-4o", "caller.x", "named", emit_log=False)
    assert res.status == "new"
    assert res.count == 1


def test_close_relocates_to_archive():
    sa.raise_alarm("canary-failed:ollama_cloud", "caller.c", "red", emit_log=False)
    assert sa.close_alarm("canary-failed:ollama_cloud") is True

    assert sa.list_alarms() == []
    archived = sa.list_archived()
    assert len(archived) == 1
    assert archived[0]["signature"] == "canary-failed:ollama_cloud"
    assert "closed_at" in archived[0]
    # closing a non-existent open alarm is a no-op, not an error
    assert sa.close_alarm("nope:none") is False


def test_reopen_detected_after_archive():
    sa.raise_alarm("no-provider:analyst", "caller.d", "down", emit_log=False)
    sa.close_alarm("no-provider:analyst")
    res = sa.raise_alarm("no-provider:analyst", "caller.d", "again", emit_log=False)
    assert res.status == "reopened"


def test_self_clear_prunes_quiet_caller_and_ages_out():
    t0 = datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)
    # Two callers at t0
    sa.raise_alarm("no-provider:designer", "stale.caller", "down", emit_log=False, now=t0)
    sa.raise_alarm("no-provider:designer", "live.caller", "down", emit_log=False, now=t0)
    # live.caller fires again much later
    t_late = t0 + timedelta(hours=30)
    sa.raise_alarm("no-provider:designer", "live.caller", "down", emit_log=False, now=t_late)

    # Prune at t_late with a 24h window: stale.caller (last seen t0) drops off.
    summary = sa.prune_stale(now=t_late, caller_quiet=timedelta(hours=24))
    assert summary["callers_pruned"] == 1
    rec = sa.get_alarm("no-provider:designer")
    assert rec is not None
    assert "stale.caller" not in rec["callers"]
    assert "live.caller" in rec["callers"]

    # Now everything goes quiet; far-future prune empties the breakdown → aged out.
    t_future = t_late + timedelta(hours=48)
    summary2 = sa.prune_stale(now=t_future, caller_quiet=timedelta(hours=24))
    assert summary2["alarms_aged_out"] == 1
    assert sa.get_alarm("no-provider:designer") is None  # disappeared = resolved
    assert any(r["signature"] == "no-provider:designer" for r in sa.list_archived())


def test_raise_alarm_emits_log_line(caplog):
    with caplog.at_level(logging.ERROR, logger="unseen_university.system_alarms"):
        sa.raise_alarm("no-provider:minion", "caller.e", "no source", emit_log=True)
    assert any("SYSTEM_ALARM" in r.message and "no-provider:minion" in r.message
               for r in caplog.records)


def test_drop_failure_never_raises(monkeypatch, caplog):
    """If the artifact write fails, the caller must not see an exception and the
    log line must still go out."""
    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(sa, "_atomic_write", _boom)
    with caplog.at_level(logging.ERROR, logger="unseen_university.system_alarms"):
        res = sa.raise_alarm("no-provider:worker", "caller.f", "down", emit_log=True)
    # No file written, status reflects failure, but no exception propagated.
    assert res.status == "error"
    assert sa.list_alarms() == []
    assert any("SYSTEM_ALARM" in r.message for r in caplog.records)


def test_fatal_drops_then_raises():
    """fatal=True reports the alarm (durably) THEN raises to halt the caller."""
    with pytest.raises(sa.SystemAlarmFatal):
        sa.raise_alarm("no-provider:worker", "caller.g", "unrecoverable",
                       fatal=True, emit_log=False)
    # reported before the throw — the artifact is on disk despite the raise
    rec = sa.get_alarm("no-provider:worker")
    assert rec is not None
    assert rec["callers"] == {"caller.g": 1}


def test_non_fatal_never_raises():
    """Default fatal=False returns normally and never raises."""
    res = sa.raise_alarm("no-provider:analyst", "caller.h", "down", emit_log=False)
    assert res.status in ("new", "incremented")


def test_ordinary_logging_drops_no_alarm():
    """Only raise_alarm drops a file; plain logging must not."""
    logging.getLogger("unseen_university.system_alarms").error("just an error")
    assert sa.list_alarms() == []
