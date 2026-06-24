"""Tests for T-vetinari-deployment-signal: VETINARI_COMPLETE channel post."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_device(tmp_path):
    import devices.vetinari.device as _vd; _vd.uu_home = lambda p=str(tmp_path): p
    from devices.vetinari.device import VetinariDevice
    channel_calls = []
    v = VetinariDevice(channel_post_fn=lambda msg: channel_calls.append(msg))
    return v, channel_calls


def _seed_active(v, tmp_path, child_ids):
    v.accept_directive({"id": "dir-signal", "text": "do stuff", "from": "akien", "received_at": "2026-06-08T00:00:00+00:00"})
    directives = v.get_pending_directives()
    path = Path(tmp_path) / "vetinari" / "pending_directives.json"
    for d in directives:
        if d["id"] == "dir-signal":
            d["status"] = "active"
            d["child_ticket_ids"] = child_ids
    path.write_text(json.dumps(directives, indent=2))


def _mock_show(status_map):
    def fake_run(cmd, **kwargs):
        ticket_id = cmd[-1]
        status = status_map.get(ticket_id)
        result = MagicMock()
        result.returncode = 0 if status else 1
        result.stdout = json.dumps({"id": ticket_id, "status": status}) if status else ""
        result.stderr = ""
        return result
    return fake_run


def test_complete_posts_vetinari_complete_to_channel(tmp_path):
    v, channel_calls = _make_device(tmp_path)
    _seed_active(v, tmp_path, ["T-a", "T-b"])
    with patch("devices.vetinari.device.subprocess.run", side_effect=_mock_show({"T-a": "closed", "T-b": "closed"})):
        v.check_directive_progress("dir-signal")
    assert any("VETINARI_COMPLETE" in msg for msg in channel_calls)
    complete_msg = next(m for m in channel_calls if "VETINARI_COMPLETE" in m)
    assert "dir-signal" in complete_msg
    assert "tickets=2" in complete_msg


def test_complete_sets_completed_at_in_directive_state(tmp_path):
    v, _ = _make_device(tmp_path)
    _seed_active(v, tmp_path, ["T-a"])
    with patch("devices.vetinari.device.subprocess.run", side_effect=_mock_show({"T-a": "closed"})):
        v.check_directive_progress("dir-signal")
    directives = v.get_pending_directives()
    d = next(d for d in directives if d["id"] == "dir-signal")
    assert "completed_at" in d
    assert d["completed_at"] is not None


def test_second_call_does_not_repost_completion(tmp_path):
    """Idempotent: VETINARI_COMPLETE fires exactly once even after multiple calls."""
    v, channel_calls = _make_device(tmp_path)
    _seed_active(v, tmp_path, ["T-a"])
    status_map = {"T-a": "closed"}
    with patch("devices.vetinari.device.subprocess.run", side_effect=_mock_show(status_map)):
        v.check_directive_progress("dir-signal")  # first call → posts
        v.check_directive_progress("dir-signal")  # second call → no repost
    complete_count = sum(1 for m in channel_calls if "VETINARI_COMPLETE" in m)
    assert complete_count == 1


def test_partial_close_does_not_fire_complete(tmp_path):
    v, channel_calls = _make_device(tmp_path)
    _seed_active(v, tmp_path, ["T-a", "T-b"])
    with patch("devices.vetinari.device.subprocess.run", side_effect=_mock_show({"T-a": "closed", "T-b": "sprint"})):
        v.check_directive_progress("dir-signal")
    assert not any("VETINARI_COMPLETE" in m for m in channel_calls)


def test_complete_produces_audit_entry(tmp_path):
    """Completion produces a COMPLETE audit entry."""
    v, _ = _make_device(tmp_path)
    _seed_active(v, tmp_path, ["T-a"])
    with patch("devices.vetinari.device.subprocess.run", side_effect=_mock_show({"T-a": "closed"})):
        v.check_directive_progress("dir-signal")
    entries = v.get_audit_log(directive_id="dir-signal")
    complete_entries = [e for e in entries if e["event"] == "COMPLETE"]
    assert len(complete_entries) == 1
