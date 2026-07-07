"""Red->green: a LATE handshake reply must not REGRESS a ticket the builder already
advanced (T-consequence-model drain-stall, 2026-07-07).

A fast builder (aider producing 0 edits) escalates BEFORE Granny processes its
'dispatch_started' reply a cycle later. Applying that stale 'started' overwrote
'escalated' back to 'in_progress', leaving the worker permanently _worker_busy and
starving the drain. The handshake handler must only apply a transition from its
expected pre-state.

Without the guard, `test_started_does_not_regress_escalated` fails (the ticket is
dragged back to in_progress).
"""

from unseen_university.devices.granny import daemon
from unseen_university import ticket_store


class _Env:
    def __init__(self, payload):
        self.payload = payload


class _Bus:
    def __init__(self, envs):
        self._envs = envs

    def fetch_unseen(self, mailbox):
        return self._envs


def _started(tid):
    return _Bus([_Env({"kind": "dispatch_started", "ticket_id": tid})])


def test_started_does_not_regress_escalated(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    ticket_store.write({"id": "T-esc", "status": "escalated", "title": "t", "role": "builder"})
    daemon._process_handshake_replies(_started("T-esc"), "granny.0")
    assert ticket_store.read("T-esc")["status"] == "escalated", "must NOT regress a terminal state"


def test_started_does_not_regress_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    ticket_store.write({"id": "T-cl", "status": "closed", "title": "t", "role": "builder"})
    daemon._process_handshake_replies(_started("T-cl"), "granny.0")
    assert ticket_store.read("T-cl")["status"] == "closed"


def test_started_applies_forward_from_dispatched(tmp_path, monkeypatch):
    # The normal path still works: dispatched -> in_progress.
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    ticket_store.write({"id": "T-disp", "status": "dispatched", "title": "t", "role": "builder"})
    daemon._process_handshake_replies(_started("T-disp"), "granny.0")
    assert ticket_store.read("T-disp")["status"] == "in_progress"
