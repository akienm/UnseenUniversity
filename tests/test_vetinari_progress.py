"""Tests for T-vetinari-progress-tracking: directive lifecycle via cc_queue polling."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch


def _make_device(tmp_path):
    import devices.vetinari.device as _vd; _vd.uu_home = lambda p=str(tmp_path): p
    from devices.vetinari.device import VetinariDevice
    return VetinariDevice(channel_post_fn=lambda m: None)


def _seed_active_directive(v, directive_id="dir-001", child_ids=None):
    v.accept_directive({
        "id": directive_id,
        "text": "build something",
        "from": "akien",
        "received_at": "2026-06-08T00:00:00+00:00",
    })
    # Manually set as active with child_ticket_ids (simulates post-decompose state)
    directives = v.get_pending_directives()
    from pathlib import Path
    path = Path(__import__("devices.vetinari.device", fromlist=["uu_home"]).uu_home()) / "vetinari" / "pending_directives.json"
    for d in directives:
        if d["id"] == directive_id:
            d["status"] = "active"
            d["child_ticket_ids"] = child_ids or ["T-child-1", "T-child-2"]
    path.write_text(json.dumps(directives, indent=2))


def _mock_show(status_map: dict):
    """Return a subprocess.run mock that returns ticket status from status_map."""
    def fake_run(cmd, **kwargs):
        ticket_id = cmd[-1]  # last arg is the ticket ID
        status = status_map.get(ticket_id)
        result = MagicMock()
        if status is None:
            result.returncode = 1
            result.stdout = ""
        else:
            result.returncode = 0
            result.stdout = json.dumps({"id": ticket_id, "status": status, "title": ticket_id})
        result.stderr = ""
        return result
    return fake_run


# ── check_directive_progress ──────────────────────────────────────────────────


def test_all_open_children_gives_active_status(tmp_path):
    v = _make_device(tmp_path)
    _seed_active_directive(v, child_ids=["T-a", "T-b"])
    status_map = {"T-a": "sprint", "T-b": "sprint"}
    with patch("devices.vetinari.device.subprocess.run", side_effect=_mock_show(status_map)):
        counts = v.check_directive_progress("dir-001")
    assert counts["open"] == 2
    assert counts["closed"] == 0
    assert v.get_directive_status("dir-001") == "active"


def test_partial_closed_gives_active_status(tmp_path):
    v = _make_device(tmp_path)
    _seed_active_directive(v, child_ids=["T-a", "T-b"])
    status_map = {"T-a": "closed", "T-b": "sprint"}
    with patch("devices.vetinari.device.subprocess.run", side_effect=_mock_show(status_map)):
        counts = v.check_directive_progress("dir-001")
    assert counts["closed"] == 1
    assert counts["open"] == 1
    assert v.get_directive_status("dir-001") == "active"


def test_all_closed_gives_completed_status(tmp_path):
    v = _make_device(tmp_path)
    _seed_active_directive(v, child_ids=["T-a", "T-b"])
    status_map = {"T-a": "closed", "T-b": "done"}
    with patch("devices.vetinari.device.subprocess.run", side_effect=_mock_show(status_map)):
        counts = v.check_directive_progress("dir-001")
    assert counts["closed"] == 2
    assert v.get_directive_status("dir-001") == "completed"


def test_missing_ticket_counted_gracefully(tmp_path):
    """Ticket not found in cc_queue: counted as 'missing', not fatal."""
    v = _make_device(tmp_path)
    _seed_active_directive(v, child_ids=["T-exists", "T-missing"])
    status_map = {"T-exists": "sprint"}  # T-missing will return returncode=1
    with patch("devices.vetinari.device.subprocess.run", side_effect=_mock_show(status_map)):
        counts = v.check_directive_progress("dir-001")
    assert counts["missing"] == 1
    assert counts["open"] == 1


def test_progress_snapshot_persisted_to_flat_file(tmp_path):
    """check_directive_progress writes progress dict to pending_directives.json."""
    v = _make_device(tmp_path)
    _seed_active_directive(v, child_ids=["T-a"])
    status_map = {"T-a": "sprint"}
    with patch("devices.vetinari.device.subprocess.run", side_effect=_mock_show(status_map)):
        v.check_directive_progress("dir-001")
    directives = v.get_pending_directives()
    d = next(d for d in directives if d["id"] == "dir-001")
    assert "progress" in d
    assert d["progress"]["open"] == 1


def test_completed_at_set_when_all_closed(tmp_path):
    """When directive completes, completed_at timestamp is recorded."""
    v = _make_device(tmp_path)
    _seed_active_directive(v, child_ids=["T-a"])
    status_map = {"T-a": "closed"}
    with patch("devices.vetinari.device.subprocess.run", side_effect=_mock_show(status_map)):
        v.check_directive_progress("dir-001")
    directives = v.get_pending_directives()
    d = next(d for d in directives if d["id"] == "dir-001")
    assert "completed_at" in d
    assert d["completed_at"] is not None


def test_get_directive_status_unknown_for_missing_directive(tmp_path):
    v = _make_device(tmp_path)
    assert v.get_directive_status("does-not-exist") == "unknown"


def test_check_progress_returns_empty_for_missing_directive(tmp_path):
    v = _make_device(tmp_path)
    counts = v.check_directive_progress("does-not-exist")
    assert counts == {"open": 0, "in_progress": 0, "closed": 0, "missing": 0}


def test_in_progress_children_counted_separately(tmp_path):
    v = _make_device(tmp_path)
    _seed_active_directive(v, child_ids=["T-a", "T-b", "T-c"])
    status_map = {"T-a": "in_progress", "T-b": "sprint", "T-c": "closed"}
    with patch("devices.vetinari.device.subprocess.run", side_effect=_mock_show(status_map)):
        counts = v.check_directive_progress("dir-001")
    assert counts["in_progress"] == 1
    assert counts["open"] == 1
    assert counts["closed"] == 1
    assert v.get_directive_status("dir-001") == "active"
