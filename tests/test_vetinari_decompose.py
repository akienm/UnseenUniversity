"""Tests for T-vetinari-decompose: LLM decomposition of directives into cc_queue tickets.

All tests inject mock LLM functions — no live OR call, no real cc_queue writes.
The _write_tickets_to_queue subprocess call is patched to capture what would be written.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch, MagicMock


def _make_device(tmp_path):
    import unseen_university.devices.vetinari.device as _vd; _vd.uu_home = lambda p=str(tmp_path): p
    from unseen_university.devices.vetinari.device import VetinariDevice
    return VetinariDevice(channel_post_fn=lambda m: None)


def _seed_directive(v, directive_id="dir-001", text="build the web server"):
    v.accept_directive({
        "id": directive_id,
        "text": text,
        "from": "akien",
        "received_at": "2026-06-08T00:00:00+00:00",
    })


def _mock_llm_ok(text: str) -> str:
    return json.dumps([
        {
            "title": "Implement web server endpoint",
            "description": "Add GET /health endpoint",
            "worker": "claude",
            "tags": ["Infrastructure"],
            "size": "S",
        },
        {
            "title": "Write integration tests",
            "description": "Test the health endpoint end-to-end",
            "worker": "claude",
            "tags": ["Testing"],
            "size": "S",
        },
    ])


def _mock_llm_malformed(_text: str) -> str:
    return "this is not json at all"


def _mock_llm_empty(_text: str) -> str:
    return "[]"


def _mock_llm_raises(_text: str) -> str:
    raise RuntimeError("OR API error: rate limited")


# ── _parse_subtasks (pure unit) ───────────────────────────────────────────────


def test_parse_subtasks_returns_list():
    from unseen_university.devices.vetinari.device import _parse_subtasks
    raw = json.dumps([{"title": "do thing", "description": "desc", "worker": "claude", "tags": [], "size": "S"}])
    result = _parse_subtasks(raw)
    assert isinstance(result, list)
    assert len(result) == 1


def test_parse_subtasks_strips_markdown_fences():
    from unseen_university.devices.vetinari.device import _parse_subtasks
    raw = "```json\n[{\"title\": \"t\", \"description\": \"d\", \"worker\": \"claude\", \"tags\": [], \"size\": \"S\"}]\n```"
    result = _parse_subtasks(raw)
    assert len(result) == 1
    assert result[0]["title"] == "t"


def test_parse_subtasks_raises_on_invalid_json():
    from unseen_university.devices.vetinari.device import _parse_subtasks
    import pytest
    with pytest.raises(ValueError, match="not valid JSON"):
        _parse_subtasks("not json")


def test_parse_subtasks_raises_on_empty_list():
    from unseen_university.devices.vetinari.device import _parse_subtasks
    import pytest
    with pytest.raises(ValueError, match="empty"):
        _parse_subtasks("[]")


def test_parse_subtasks_raises_on_non_list():
    from unseen_university.devices.vetinari.device import _parse_subtasks
    import pytest
    with pytest.raises(ValueError, match="not a JSON array"):
        _parse_subtasks('{"title": "single object"}')


# ── decompose_directive (injectable LLM) ─────────────────────────────────────


def test_decompose_produces_child_ticket_ids(tmp_path):
    """decompose_directive returns a list of ticket IDs when LLM succeeds."""
    v = _make_device(tmp_path)
    _seed_directive(v)
    with patch("unseen_university.devices.vetinari.device._write_tickets_to_queue", return_value=["T-vetinari-implement-web-server", "T-vetinari-write-integration-tests"]):
        ids = v.decompose_directive("dir-001", llm_fn=_mock_llm_ok)
    assert len(ids) >= 1
    assert all(isinstance(i, str) for i in ids)


def test_decompose_transitions_directive_to_active(tmp_path):
    """After decompose, directive status == 'active'."""
    v = _make_device(tmp_path)
    _seed_directive(v)
    with patch("unseen_university.devices.vetinari.device._write_tickets_to_queue", return_value=["T-vetinari-implement-web-server"]):
        v.decompose_directive("dir-001", llm_fn=_mock_llm_ok)
    directives = v.get_pending_directives()
    d = next(d for d in directives if d["id"] == "dir-001")
    assert d["status"] == "active"


def test_decompose_records_child_ticket_ids(tmp_path):
    """After decompose, directive has child_ticket_ids list."""
    v = _make_device(tmp_path)
    _seed_directive(v)
    fake_ids = ["T-vetinari-endpoint", "T-vetinari-tests"]
    with patch("unseen_university.devices.vetinari.device._write_tickets_to_queue", return_value=fake_ids):
        v.decompose_directive("dir-001", llm_fn=_mock_llm_ok)
    directives = v.get_pending_directives()
    d = next(d for d in directives if d["id"] == "dir-001")
    assert d["child_ticket_ids"] == fake_ids


def test_decompose_raises_for_unknown_directive_id(tmp_path):
    import pytest
    v = _make_device(tmp_path)
    with pytest.raises(ValueError, match="not found"):
        v.decompose_directive("does-not-exist", llm_fn=_mock_llm_ok)


def test_decompose_retries_on_llm_parse_error_then_raises(tmp_path):
    """When LLM returns unparseable response twice, raises ValueError."""
    import pytest
    v = _make_device(tmp_path)
    _seed_directive(v)
    with pytest.raises(ValueError):
        v.decompose_directive("dir-001", llm_fn=_mock_llm_malformed)


def test_decompose_raises_when_llm_raises(tmp_path):
    """When LLM call raises, decompose retries and then raises."""
    import pytest
    v = _make_device(tmp_path)
    _seed_directive(v)
    with pytest.raises(ValueError):
        v.decompose_directive("dir-001", llm_fn=_mock_llm_raises)


def test_decompose_raises_on_empty_subtask_list(tmp_path):
    """LLM returning [] after retry → raises (can't make zero tickets)."""
    import pytest
    v = _make_device(tmp_path)
    _seed_directive(v)
    with pytest.raises(ValueError):
        v.decompose_directive("dir-001", llm_fn=_mock_llm_empty)
