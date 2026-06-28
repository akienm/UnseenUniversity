"""Tests for T-vetinari-cp-audit: CP3/CP6 decision audit log."""

from __future__ import annotations

import json
import os
from unittest.mock import patch


def _make_device(tmp_path):
    import unseen_university.devices.vetinari.device as _vd; _vd.uu_home = lambda p=str(tmp_path): p
    from unseen_university.devices.vetinari.device import VetinariDevice
    return VetinariDevice(channel_post_fn=lambda m: None)


def test_audit_log_appends_valid_jsonl(tmp_path):
    v = _make_device(tmp_path)
    v._audit_log("ROUTE", "tag=Build matched claude", {"worker": "claude"}, directive_id="d-1")
    path = tmp_path / "vetinari" / "audit.jsonl"
    assert path.exists()
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["event"] == "ROUTE"
    assert entry["reason"] == "tag=Build matched claude"
    assert entry["directive_id"] == "d-1"
    assert "ts" in entry


def test_audit_log_is_append_only(tmp_path):
    v = _make_device(tmp_path)
    v._audit_log("ROUTE", "first", {}, directive_id="d-1")
    v._audit_log("DECOMPOSE", "second", {}, directive_id="d-2")
    path = tmp_path / "vetinari" / "audit.jsonl"
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    assert len(lines) == 2


def test_get_audit_log_filters_by_directive_id(tmp_path):
    v = _make_device(tmp_path)
    v._audit_log("ROUTE", "r1", {}, directive_id="alpha")
    v._audit_log("ROUTE", "r2", {}, directive_id="beta")
    v._audit_log("DECOMPOSE", "d1", {}, directive_id="alpha")
    alpha_entries = v.get_audit_log(directive_id="alpha")
    assert len(alpha_entries) == 2
    assert all(e["directive_id"] == "alpha" for e in alpha_entries)


def test_get_audit_log_no_filter_returns_all(tmp_path):
    v = _make_device(tmp_path)
    v._audit_log("ROUTE", "r1", {}, directive_id="alpha")
    v._audit_log("ROUTE", "r2", {}, directive_id="beta")
    all_entries = v.get_audit_log()
    assert len(all_entries) == 2


def test_get_audit_log_empty_when_no_file(tmp_path):
    v = _make_device(tmp_path)
    assert v.get_audit_log() == []


def test_decompose_produces_audit_entry(tmp_path):
    """After decompose_directive(), audit.jsonl has a DECOMPOSE entry."""
    v = _make_device(tmp_path)
    v.accept_directive({"id": "d-audit", "text": "build something", "from": "akien", "received_at": "2026-06-08T00:00:00+00:00"})

    def mock_llm(_text):
        import json
        return json.dumps([{"title": "Do the thing", "description": "d", "tags": ["Build"], "size": "S"}])

    with patch("unseen_university.devices.vetinari.device._write_tickets_to_queue", return_value=["T-vetinari-do-the-thing"]):
        v.decompose_directive("d-audit", llm_fn=mock_llm)

    entries = v.get_audit_log(directive_id="d-audit")
    decompose_entries = [e for e in entries if e["event"] == "DECOMPOSE"]
    assert len(decompose_entries) >= 1
    assert "child_ticket_ids" in decompose_entries[0]["context"]


def test_escalation_produces_audit_entry(tmp_path):
    """_escalate_to_akien() logs an ESCALATE event."""
    v = _make_device(tmp_path)
    v.own_factory("test-factory", {})
    v.receive_health_rollup("test-factory", {"eval_score": 0.1, "status": "unhealthy"})
    entries = v.get_audit_log()
    escalate_entries = [e for e in entries if e["event"] == "ESCALATE"]
    assert len(escalate_entries) >= 1
    assert escalate_entries[0]["context"]["factory_id"] == "test-factory"
