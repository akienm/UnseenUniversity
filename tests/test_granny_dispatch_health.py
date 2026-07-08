"""Proof for T-granny-dispatch-observability-gap.

Two halves, both hermetic:
  1. The pure ``summarize_dispatch_health`` fires the idle-builder WARN on the
     exact observed condition (1 available builder idle 16h, dispatchable_by_target=0,
     deferred_unavailable=217) — the offload-loop-stalled signature. This is the
     proof node: the stub returns no warns, so the red run fails on the assert.
  2. ``configure_process_logging`` routes a standalone process's stdlib log record
     into the canonical per-device JSON sink (``<log_root>/granny/info/``) instead
     of the tmux pane — the stale-log fix.
"""
from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unseen_university.devices.granny.dispatch_health import (  # noqa: E402
    BuilderState,
    count_backlog,
    summarize_dispatch_health,
)

_16H = 16 * 3600
_IDLE_THRESHOLD = 4 * 3600  # WARN when an available builder idles past 4h with work waiting


def test_idle_builder_with_deferred_work_warns():
    """The observed stall: Aider.0 available + idle 16h, 217 tickets deferred to an
    unavailable CC.0, 0 targeted at Aider. Work waits; it isn't reaching the idle
    builder. That MUST surface as a WARN — this is the whole point of the ticket."""
    report = summarize_dispatch_health(
        [BuilderState(name="Aider.0", available=True, last_dispatch_age_s=_16H)],
        dispatchable_by_target=0,
        deferred_unavailable=217,
        idle_threshold_s=_IDLE_THRESHOLD,
    )
    assert report.warns, "an available builder idle 16h with 217 tickets waiting must WARN"
    warn = report.warns[0]
    assert "Aider.0" in warn
    assert "217" in warn  # names the waiting backlog
    assert "16" in warn or "16.0h" in warn  # names the idle age


def test_no_warn_when_backlog_empty():
    """Idle builder but nothing waiting → healthy idle, no alarm (calm signal)."""
    report = summarize_dispatch_health(
        [BuilderState(name="Aider.0", available=True, last_dispatch_age_s=_16H)],
        dispatchable_by_target=0,
        deferred_unavailable=0,
        idle_threshold_s=_IDLE_THRESHOLD,
    )
    assert report.warns == []


def test_no_warn_when_builder_recently_dispatched():
    """Builder took work within the threshold → not idle, no WARN even with backlog."""
    report = summarize_dispatch_health(
        [BuilderState(name="Aider.0", available=True, last_dispatch_age_s=60)],
        dispatchable_by_target=5,
        deferred_unavailable=10,
        idle_threshold_s=_IDLE_THRESHOLD,
    )
    assert report.warns == []


def test_no_warn_when_builder_unavailable():
    """An unavailable builder isn't expected to take work — its idleness is not the
    signal (that's a different problem). Only AVAILABLE-but-idle-with-work warns."""
    report = summarize_dispatch_health(
        [BuilderState(name="Aider.0", available=False, last_dispatch_age_s=_16H)],
        dispatchable_by_target=0,
        deferred_unavailable=217,
        idle_threshold_s=_IDLE_THRESHOLD,
    )
    assert report.warns == []


def test_info_line_shows_glanceable_counts():
    """The health line answers 'who's available, how long idle, how much waiting'
    in one glance."""
    report = summarize_dispatch_health(
        [
            BuilderState(name="Aider.0", available=True, last_dispatch_age_s=_16H),
            BuilderState(name="DickSimnel.0", available=False, last_dispatch_age_s=None),
        ],
        dispatchable_by_target=0,
        deferred_unavailable=217,
        idle_threshold_s=_IDLE_THRESHOLD,
    )
    line = report.info_line
    assert "available=1/2" in line
    assert "dispatchable_by_target=0" in line
    assert "deferred_unavailable=217" in line


def test_count_backlog_splits_by_target_availability():
    """The observed stall: 217 tickets all targeting an unavailable CC.0 → all
    deferred, zero dispatchable. Plus one targeting an available builder."""
    tickets = [{"id": f"T-{i}", "target": "CC.0"} for i in range(217)]
    tickets.append({"id": "T-live", "target": "Aider.0"})
    avail = {"CC.0": False, "Aider.0": True}
    dispatchable, deferred = count_backlog(
        tickets,
        target_of=lambda t: t["target"],
        is_available=lambda w: avail.get(w, False),
    )
    assert (dispatchable, deferred) == (1, 217)


def test_count_backlog_ignores_unroutable_tickets():
    """A ticket with no resolvable target is a routing problem, not availability —
    it is counted in neither bucket."""
    tickets = [{"id": "T-x"}, {"id": "T-y", "target": "Aider.0"}]
    dispatchable, deferred = count_backlog(
        tickets,
        target_of=lambda t: t.get("target"),
        is_available=lambda w: True,
    )
    assert (dispatchable, deferred) == (1, 0)


def test_last_dispatch_age_roundtrip(tmp_path, monkeypatch):
    """record_dispatch_time then last_dispatch_age_s returns a small positive age;
    an unknown worker returns None (never treated as idle)."""
    from datetime import datetime, timedelta, timezone
    import unseen_university.devices.granny.stall_state as ss

    monkeypatch.setattr(ss, "_LAST_DISPATCH", tmp_path / "last_dispatch.json")
    t0 = datetime(2026, 7, 8, 12, 0, 0, tzinfo=timezone.utc)
    ss.record_dispatch_time("Aider.0", now=t0)

    age = ss.last_dispatch_age_s("Aider.0", now=t0 + timedelta(hours=16))
    assert age == 16 * 3600
    assert ss.last_dispatch_age_s("Never.0", now=t0) is None


def test_configure_process_logging_routes_stdlib_to_canonical_json(tmp_path, monkeypatch):
    """A standalone daemon's stdlib log record must land in the canonical per-device
    JSON log (<log_root>/granny/<stream>/), not vanish to the tmux pane."""
    monkeypatch.setenv("UU_LOG_ROOT", str(tmp_path))
    from unseen_university.diagnostic_base.base import configure_process_logging

    configure_process_logging("granny")
    logging.getLogger("granny.test").info(
        "Granny: dispatch_defer|ticket=T-x|target=CC.0|reason=unavailable"
    )

    files = list((tmp_path / "granny" / "info").glob("*.json"))
    assert files, "the dispatch decision must be written to the canonical granny log"
    payload = json.loads(files[0].read_text())
    assert payload["device_id"] == "granny"
    assert "dispatch_defer" in payload["message"]
