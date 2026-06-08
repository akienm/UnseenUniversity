"""Tests for T-vetinari-directive-intake: VetinariShim + directive intake.

Completion criteria: receive, persist, restart-survives, malformed-envelope-graceful,
duplicate-id-handled. All tests use tmp_path + stub IMAP — no live bus needed.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock


def _make_device(tmp_path):
    """Return a VetinariDevice rooted at tmp_path."""
    os.environ["IGOR_HOME"] = str(tmp_path)
    from devices.vetinari.device import VetinariDevice
    return VetinariDevice(channel_post_fn=lambda m: None)


def _directive(**kw) -> dict:
    base = {"id": "dir-001", "text": "deploy the web server", "from": "akien", "received_at": "2026-06-08T00:00:00+00:00"}
    base.update(kw)
    return base


# ── Device: accept_directive ──────────────────────────────────────────────────


def test_accept_directive_creates_file(tmp_path):
    v = _make_device(tmp_path)
    v.accept_directive(_directive())
    path = tmp_path / "vetinari" / "pending_directives.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert len(data) == 1
    assert data[0]["id"] == "dir-001"


def test_accept_directive_returns_true_on_add(tmp_path):
    v = _make_device(tmp_path)
    result = v.accept_directive(_directive())
    assert result is True


def test_accept_directive_duplicate_id_rejected(tmp_path):
    v = _make_device(tmp_path)
    v.accept_directive(_directive(id="dup-1"))
    result = v.accept_directive(_directive(id="dup-1", text="different text"))
    assert result is False
    directives = v.get_pending_directives()
    assert len(directives) == 1  # only one entry despite two accept calls


def test_accept_directive_multiple_unique_ids(tmp_path):
    v = _make_device(tmp_path)
    v.accept_directive(_directive(id="d-1", text="first"))
    v.accept_directive(_directive(id="d-2", text="second"))
    directives = v.get_pending_directives()
    assert len(directives) == 2
    ids = {d["id"] for d in directives}
    assert ids == {"d-1", "d-2"}


def test_get_pending_directives_survives_restart(tmp_path):
    """Directives persisted by one instance are readable by a new instance."""
    v1 = _make_device(tmp_path)
    v1.accept_directive(_directive(id="persist-me", text="build the thing"))
    # Create fresh instance (simulates restart)
    os.environ["IGOR_HOME"] = str(tmp_path)
    from devices.vetinari.device import VetinariDevice
    v2 = VetinariDevice(channel_post_fn=lambda m: None)
    directives = v2.get_pending_directives()
    assert any(d["id"] == "persist-me" for d in directives)


def test_get_pending_directives_empty_when_no_file(tmp_path):
    v = _make_device(tmp_path)
    assert v.get_pending_directives() == []


def test_accept_directive_malformed_json_in_file_recovers(tmp_path):
    """If pending_directives.json is corrupted, accept_directive reinitialises it."""
    path = tmp_path / "vetinari" / "pending_directives.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{corrupt: json}")  # invalid JSON
    v = _make_device(tmp_path)
    result = v.accept_directive(_directive())
    assert result is True
    directives = v.get_pending_directives()
    assert len(directives) == 1


# ── DirectiveListener: _parse_directive + _process ───────────────────────────


def test_parse_directive_from_json_bytes():
    from devices.vetinari.shim import _parse_directive
    raw = json.dumps({
        "from_device": "akien",
        "to_device": "vetinari",
        "sent_at": "2026-06-08T00:00:00+00:00",
        "schema_version": "1",
        "payload": {"text": "build the web server", "id": "d-from-bytes"},
    }).encode()
    directive = _parse_directive(raw)
    assert directive["text"] == "build the web server"
    assert directive["id"] == "d-from-bytes"
    assert directive["from"] == "akien"


def test_parse_directive_malformed_raises_value_error():
    from devices.vetinari.shim import _parse_directive
    import pytest
    with pytest.raises(ValueError):
        _parse_directive(b"not json at all")


def test_parse_directive_empty_payload_raises_value_error():
    from devices.vetinari.shim import _parse_directive
    import pytest
    raw = json.dumps({
        "from_device": "akien",
        "to_device": "vetinari",
        "sent_at": "2026-06-08T00:00:00+00:00",
        "schema_version": "1",
        "payload": {},  # no text or directive field
    }).encode()
    with pytest.raises(ValueError):
        _parse_directive(raw)


def test_listener_process_malformed_envelope_does_not_raise(tmp_path):
    """DirectiveListener._process with bad input logs and continues, never raises."""
    from devices.vetinari.shim import DirectiveListener
    v = _make_device(tmp_path)
    stub_imap = MagicMock()
    listener = DirectiveListener(device=v, imap=stub_imap)
    listener._process(b"totally invalid garbage envelope")  # must not raise
    assert v.get_pending_directives() == []  # nothing persisted


def test_listener_process_valid_envelope_persists_directive(tmp_path):
    from devices.vetinari.shim import DirectiveListener
    v = _make_device(tmp_path)
    stub_imap = MagicMock()
    listener = DirectiveListener(device=v, imap=stub_imap)
    raw = json.dumps({
        "from_device": "akien",
        "to_device": "vetinari",
        "sent_at": "2026-06-08T03:00:00+00:00",
        "schema_version": "1",
        "payload": {"text": "start the sprint", "id": "sprint-directive"},
    }).encode()
    listener._process(raw)
    directives = v.get_pending_directives()
    assert len(directives) == 1
    assert directives[0]["id"] == "sprint-directive"


# ── VetinariShim: lifecycle ───────────────────────────────────────────────────


def test_vetinari_shim_starts_without_error(tmp_path):
    """VetinariShim.start() with stub IMAP returns True and thread is alive."""
    os.environ["IGOR_HOME"] = str(tmp_path)
    from devices.vetinari.shim import VetinariShim
    stub_imap = MagicMock()
    stub_imap.fetch_unseen.return_value = []
    shim = VetinariShim(imap=stub_imap)
    try:
        result = shim.start()
        assert result is True
        test_result = shim.self_test()
        assert test_result["passed"] is True
    finally:
        shim.stop()


def test_vetinari_shim_stop_cleans_up_thread(tmp_path):
    os.environ["IGOR_HOME"] = str(tmp_path)
    from devices.vetinari.shim import VetinariShim
    stub_imap = MagicMock()
    stub_imap.fetch_unseen.return_value = []
    shim = VetinariShim(imap=stub_imap)
    shim.start()
    shim.stop()
    assert shim._thread is None


def test_vetinari_shim_device_id():
    from devices.vetinari.shim import VetinariShim
    shim = VetinariShim(imap=MagicMock())
    assert shim.device_id == "vetinari"
