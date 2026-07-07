"""Granny reconciles the builder's result artifact — Granny is the SOLE writer
(D-granny-sole-ticket-writer-2026-07-07). The builder REPORTS {outcome,...}; Granny
commits it. Idempotent: a no-op once the ticket is terminal.
"""

from unseen_university.devices.granny import daemon
from unseen_university import ticket_store


def test_reconcile_escalated_sets_escalated(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    monkeypatch.setattr(daemon, "_cc_queue", lambda *a: True)  # skip subprocess append-note
    ticket_store.write({"id": "T-e", "status": "in_progress", "title": "t", "role": "builder"})
    daemon._reconcile_builder_result("T-e", {"outcome": "escalated", "reason": "0 edits",
                                             "from_device": "aider.0"})
    assert ticket_store.read("T-e")["status"] == "escalated"


def test_reconcile_done_closes_shipped_unproven_with_lever(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    calls = []
    monkeypatch.setattr(daemon, "_cc_queue", lambda *a: calls.append(a) or True)
    ticket_store.write({"id": "T-d", "status": "in_progress", "title": "t", "role": "builder"})
    daemon._reconcile_builder_result("T-d", {"outcome": "done", "branch": "aider/T-d-1",
                                            "note": "gate PASS", "missing_lever": "proof at merge",
                                            "from_device": "aider.0"})
    close = next((c for c in calls if c and c[0] == "close"), None)
    assert close is not None, "Granny must close on outcome=done"
    assert "--shipped-unproven" in close, "branch-builder close is shipped-unproven"
    assert any("proof at merge" in str(a) for a in close), "must carry the builder's lever"


def test_reconcile_skips_already_terminal(tmp_path, monkeypatch):
    # Redelivery / out-of-order: a done artifact for an already-closed ticket is a no-op
    # (no double-close, no regress).
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    calls = []
    monkeypatch.setattr(daemon, "_cc_queue", lambda *a: calls.append(a) or True)
    ticket_store.write({"id": "T-c", "status": "closed", "title": "t", "role": "builder"})
    daemon._reconcile_builder_result("T-c", {"outcome": "done", "from_device": "aider.0"})
    assert calls == [], "must not re-close a terminal ticket"
    assert ticket_store.read("T-c")["status"] == "closed"
