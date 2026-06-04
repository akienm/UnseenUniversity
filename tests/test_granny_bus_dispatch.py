"""Integration tests for Granny bus-envelope dispatch handshake.

Completion criteria:
  - Granny dispatches via bus envelope → ticket setstatus dispatched
  - Shim ack reply → ticket setstatus acked
  - Shim started reply → ticket setstatus in_progress
  - Shim timeout reply → ticket setstatus escalated
  - No ack within DISPATCH_ACK_TIMEOUT_S → setstatus escalated (watchdog)
  - dispatch=bus with imap=None → ticket skipped, log warning emitted
  - _cc0_busy() returns True for dispatched, acked, and in_progress statuses
"""

from __future__ import annotations

import collections
from unittest.mock import patch

import pytest

from bus.envelope import Envelope
from devices.granny.daemon import (
    DISPATCH_ACK_TIMEOUT_S,
    _GRANNY_MAILBOX_DEFAULT,
    _cc0_busy,
    _dispatch_bus,
    _escalate_stale_dispatched,
    _process_handshake_replies,
    _prune_dispatched,
    _reset_stale_inprogress,
    run_once,
    _default_config,
)


# ── Fake in-memory IMAP ────────────────────────────────────────────────────────


class _FakeIMAP:
    """Shared in-memory bus: append to a mailbox, fetch_unseen drains it."""

    def __init__(self):
        self._boxes: dict[str, list] = collections.defaultdict(list)
        self._read: dict[str, int] = collections.defaultdict(int)

    def append(self, mailbox: str, envelope) -> None:
        self._boxes[mailbox].append(envelope)

    def fetch_unseen(self, mailbox: str) -> list:
        seen_up_to = self._read[mailbox]
        new = self._boxes[mailbox][seen_up_to:]
        self._read[mailbox] = len(self._boxes[mailbox])
        return new

    def inject_reply(self, to_mailbox: str, kind: str, ticket_id: str) -> None:
        """Simulate a shim posting a handshake reply to Granny's mailbox."""
        env = Envelope.now(
            from_device="cc.0",
            to_device=to_mailbox,
            payload={"kind": kind, "ticket_id": ticket_id, "from_device": "cc.0"},
        )
        self.append(to_mailbox, env)


# ── Helpers ────────────────────────────────────────────────────────────────────

_OK = type("R", (), {"returncode": 0, "stderr": ""})()


def _mock_run_factory():
    """Returns (side_effect_fn, setstatus_calls_list)."""
    calls = []

    def side_effect(cmd, **kwargs):
        if "setstatus" in cmd:
            calls.append(list(cmd[3:]))
        return _OK

    return side_effect, calls


def _make_pg_conn(rows: list[dict]):
    """Minimal fake psycopg2 connection that returns rows from fetchall."""
    cur = type("Cur", (), {
        "execute": lambda s, *a: None,
        "fetchall": lambda s: rows,
        "fetchone": lambda s: rows[0] if rows else None,
        "__enter__": lambda s: s,
        "__exit__": lambda s, *a: None,
    })()
    return type("Conn", (), {
        "cursor": lambda self, **kw: cur,
        "close": lambda self: None,
    })()


# ── _prune_dispatched ─────────────────────────────────────────────────────────


class TestPruneDispatched:
    def _conn_with_active(self, active_ids: list[str]):
        """Fake conn where fetchall returns rows for the given active_ids."""
        rows = [(tid,) for tid in active_ids]
        cur = type("Cur", (), {
            "execute": lambda s, *a: None,
            "fetchall": lambda s: rows,
            "__enter__": lambda s: s,
            "__exit__": lambda s, *a: None,
        })()
        return type("Conn", (), {
            "cursor": lambda self, **kw: cur,
            "close": lambda self: None,
        })()

    def test_inactive_ids_removed(self):
        conn = self._conn_with_active(["T-active"])
        with patch("psycopg2.connect", return_value=conn):
            result = _prune_dispatched({"T-active", "T-sprint-now"})
        assert result == {"T-active"}
        assert "T-sprint-now" not in result

    def test_all_active_ids_kept(self):
        conn = self._conn_with_active(["T-a", "T-b"])
        with patch("psycopg2.connect", return_value=conn):
            result = _prune_dispatched({"T-a", "T-b"})
        assert result == {"T-a", "T-b"}

    def test_empty_input_skips_db(self):
        with patch("psycopg2.connect") as mock_pg:
            result = _prune_dispatched(set())
        mock_pg.assert_not_called()
        assert result == set()

    def test_db_failure_fails_open(self):
        with patch("psycopg2.connect", side_effect=OSError("DB down")):
            original = {"T-x", "T-y"}
            result = _prune_dispatched(original)
        assert result == original, "DB failure must return original set unchanged (fail open)"


# ── _dispatch_bus ──────────────────────────────────────────────────────────────


class TestDispatchBus:
    def test_sends_dispatch_envelope_to_worker_mailbox(self):
        imap = _FakeIMAP()
        ticket = {"id": "T-abc", "title": "test"}
        mock_run, _ = _mock_run_factory()

        with patch("devices.granny.daemon.subprocess.run", side_effect=mock_run):
            ok = _dispatch_bus(ticket, imap, "cc.0", "granny.0")

        assert ok
        envelopes = imap._boxes.get("cc.0", [])
        assert len(envelopes) == 1
        env = envelopes[0]
        assert env.payload["kind"] == "dispatch"
        assert env.payload["ticket_id"] == "T-abc"
        assert env.from_device == "granny.0"
        assert env.to_device == "cc.0"

    def test_sets_status_dispatched(self):
        imap = _FakeIMAP()
        ticket = {"id": "T-xyz"}
        mock_run, calls = _mock_run_factory()

        with patch("devices.granny.daemon.subprocess.run", side_effect=mock_run):
            _dispatch_bus(ticket, imap, "cc.0", "granny.0")

        assert ["T-xyz", "dispatched"] in calls

    def test_returns_false_on_imap_failure(self):
        class _BrokenIMAP:
            def append(self, *_):
                raise OSError("IMAP down")

        ticket = {"id": "T-fail"}
        ok = _dispatch_bus(ticket, _BrokenIMAP(), "cc.0", "granny.0")
        assert not ok

    def test_granny_mailbox_is_from_device(self):
        imap = _FakeIMAP()
        ticket = {"id": "T-mb"}
        mock_run, _ = _mock_run_factory()

        with patch("devices.granny.daemon.subprocess.run", side_effect=mock_run):
            _dispatch_bus(ticket, imap, "cc.0", "granny-custom.0")

        env = imap._boxes["cc.0"][0]
        assert env.from_device == "granny-custom.0"


# ── _process_handshake_replies ─────────────────────────────────────────────────


class TestProcessHandshakeReplies:
    def _run_with_reply(self, kind: str, ticket_id: str = "T-h"):
        imap = _FakeIMAP()
        imap.inject_reply(_GRANNY_MAILBOX_DEFAULT, kind, ticket_id)
        mock_run, calls = _mock_run_factory()

        with patch("devices.granny.daemon.subprocess.run", side_effect=mock_run):
            count = _process_handshake_replies(imap, _GRANNY_MAILBOX_DEFAULT)

        return count, calls

    def test_ack_reply_sets_acked(self):
        count, calls = self._run_with_reply("dispatch_ack", "T-a")
        assert count == 1
        assert ["T-a", "acked"] in calls

    def test_started_reply_sets_in_progress(self):
        count, calls = self._run_with_reply("dispatch_started", "T-s")
        assert count == 1
        assert ["T-s", "in_progress"] in calls

    def test_timeout_reply_sets_escalated(self):
        count, calls = self._run_with_reply("dispatch_timeout", "T-t")
        assert count == 1
        assert ["T-t", "escalated"] in calls

    def test_unknown_kind_ignored(self):
        imap = _FakeIMAP()
        imap.inject_reply(_GRANNY_MAILBOX_DEFAULT, "something_else", "T-u")
        with patch("devices.granny.daemon.subprocess.run") as mock_run:
            count = _process_handshake_replies(imap, _GRANNY_MAILBOX_DEFAULT)
        assert count == 0
        mock_run.assert_not_called()

    def test_fetch_failure_returns_zero(self):
        class _BrokenIMAP:
            def fetch_unseen(self, *_):
                raise OSError("IMAP error")

        count = _process_handshake_replies(_BrokenIMAP(), "granny.0")
        assert count == 0

    def test_replies_drain_on_each_call(self):
        imap = _FakeIMAP()
        imap.inject_reply(_GRANNY_MAILBOX_DEFAULT, "dispatch_ack", "T-d")
        mock_run, _ = _mock_run_factory()

        with patch("devices.granny.daemon.subprocess.run", side_effect=mock_run):
            first = _process_handshake_replies(imap, _GRANNY_MAILBOX_DEFAULT)
            second = _process_handshake_replies(imap, _GRANNY_MAILBOX_DEFAULT)

        assert first == 1
        assert second == 0


# ── Watchdog ───────────────────────────────────────────────────────────────────


class TestEscalateStaleDispatched:
    def test_escalates_stale_tickets(self):
        mock_run, calls = _mock_run_factory()
        conn = _make_pg_conn([{"tid": "T-stale"}])

        with patch("psycopg2.connect", return_value=conn), \
             patch("devices.granny.daemon.subprocess.run", side_effect=mock_run):
            count = _escalate_stale_dispatched()

        assert count == 1
        assert ["T-stale", "escalated"] in calls

    def test_no_stale_tickets_returns_zero(self):
        conn = _make_pg_conn([])

        with patch("psycopg2.connect", return_value=conn):
            count = _escalate_stale_dispatched()

        assert count == 0

    def test_db_failure_returns_zero(self):
        with patch("psycopg2.connect", side_effect=OSError("DB down")):
            count = _escalate_stale_dispatched()
        assert count == 0


# ── _reset_stale_inprogress ───────────────────────────────────────────────────


class TestResetStaleInprogress:
    def _capture_cmds(self, pg_rows):
        """Return (count, all_subprocess_cmds) after running _reset_stale_inprogress."""
        cmds = []

        def _run(cmd, **kwargs):
            cmds.append(list(cmd))
            return _OK

        conn = _make_pg_conn(pg_rows)
        with patch("psycopg2.connect", return_value=conn), \
             patch("devices.granny.daemon.subprocess.run", side_effect=_run):
            count = _reset_stale_inprogress()
        return count, cmds

    def test_resets_stale_ticket_via_timeout_flag(self):
        count, cmds = self._capture_cmds([{"tid": "T-stuck"}])
        assert count == 1
        # Must use reset --timeout, NOT setstatus sprint — circuit breaker depends on this
        assert any("reset" in cmd and "--timeout" in cmd and "T-stuck" in cmd for cmd in cmds), \
            "reset --timeout <tid> must be called (not setstatus sprint) to engage circuit breaker"

    def test_reset_calls_include_ticket_id(self):
        _, cmds = self._capture_cmds([{"tid": "T-abc"}])
        assert any("T-abc" in cmd and "--timeout" in cmd for cmd in cmds)

    def test_no_stale_tickets_returns_zero(self):
        conn = _make_pg_conn([])
        with patch("psycopg2.connect", return_value=conn):
            count = _reset_stale_inprogress()
        assert count == 0

    def test_db_failure_returns_zero(self):
        with patch("psycopg2.connect", side_effect=OSError("DB down")):
            count = _reset_stale_inprogress()
        assert count == 0


# ── _cc0_busy covers dispatched + acked ───────────────────────────────────────


class TestCc0BusyStatuses:
    def test_has_dispatched_means_busy(self):
        conn = _make_pg_conn([("1",)])
        with patch("psycopg2.connect", return_value=conn):
            assert _cc0_busy() is True

    def test_no_active_tickets_means_free(self):
        conn = _make_pg_conn([])
        with patch("psycopg2.connect", return_value=conn):
            assert _cc0_busy() is False

    def test_db_failure_returns_false(self):
        with patch("psycopg2.connect", side_effect=OSError("DB down")):
            assert _cc0_busy() is False


# ── run_once integration: bus dispatch path ────────────────────────────────────


class TestRunOnceBusDispatch:
    def _bus_config(self, worker_mailbox: str = "cc.0") -> dict:
        cfg = _default_config()
        cfg["workers"]["CC.0"]["dispatch"] = "bus"
        cfg["workers"]["CC.0"]["mailbox"] = worker_mailbox
        cfg["granny_mailbox"] = "granny.0"
        return cfg

    def test_dispatch_sends_envelope_and_sets_dispatched(self):
        imap = _FakeIMAP()
        ticket = {"id": "T-run", "tags": [], "role": "master"}
        mock_run, calls = _mock_run_factory()

        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("devices.granny.availability.is_available", return_value=True), \
             patch("devices.granny.daemon._cc0_busy", return_value=False), \
             patch("devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("devices.granny.daemon._post_channel"), \
             patch("devices.granny.daemon.subprocess.run", side_effect=mock_run):
            run_once(self._bus_config(), set(), imap=imap)

        assert imap._boxes.get("cc.0"), "no envelope sent to cc.0"
        assert imap._boxes["cc.0"][0].payload["kind"] == "dispatch"
        assert ["T-run", "dispatched"] in calls

    def test_ack_reply_transitions_to_acked(self):
        imap = _FakeIMAP()
        imap.inject_reply("granny.0", "dispatch_ack", "T-reply")
        mock_run, calls = _mock_run_factory()

        with patch("devices.granny.daemon._sprint_tickets", return_value=[]), \
             patch("devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("devices.granny.daemon.subprocess.run", side_effect=mock_run):
            run_once(self._bus_config(), set(), imap=imap)

        assert ["T-reply", "acked"] in calls

    def test_bus_dispatch_skipped_when_imap_none(self, caplog):
        ticket = {"id": "T-nobus", "tags": [], "role": "master"}

        with patch("devices.granny.daemon._sprint_tickets", return_value=[ticket]), \
             patch("devices.granny.availability.is_available", return_value=True), \
             patch("devices.granny.daemon._cc0_busy", return_value=False), \
             patch("devices.granny.daemon._escalate_stale_dispatched", return_value=0), \
             patch("devices.granny.daemon._post_channel"):
            import logging
            with caplog.at_level(logging.WARNING, logger="devices.granny.daemon"):
                result = run_once(self._bus_config(), set(), imap=None)

        assert "T-nobus" not in result
        assert any("no imap" in r.message for r in caplog.records)
