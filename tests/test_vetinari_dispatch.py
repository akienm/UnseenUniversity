"""Tests for T-vetinari-team-dispatch: tag-based worker routing."""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from devices.vetinari.device import ROUTING_TABLE, _route_worker


# ── _route_worker (pure) ──────────────────────────────────────────────────────


def test_build_tags_route_to_claude():
    assert _route_worker(["Build", "Architecture"]) == "claude"


def test_research_tags_route_to_librarian():
    assert _route_worker(["Research"]) == "librarian"


def test_summarize_tags_route_to_dicksimnel():
    assert _route_worker(["Summarize"]) == "dicksimnel"


def test_review_tags_route_to_dicksimnel():
    assert _route_worker(["Review"]) == "dicksimnel"


def test_empty_tags_default_to_claude():
    assert _route_worker([]) == "claude"


def test_unknown_tags_default_to_claude():
    assert _route_worker(["UnknownTag", "AnotherUnknown"]) == "claude"


def test_memory_tags_route_to_librarian():
    assert _route_worker(["Memory"]) == "librarian"


def test_first_matching_tag_wins():
    # Research comes first → librarian wins over any build tags later
    result = _route_worker(["Research", "Build"])
    assert result == "librarian"


def test_routing_is_case_insensitive():
    assert _route_worker(["build"]) == _route_worker(["Build"]) == "claude"
    assert _route_worker(["RESEARCH"]) == "librarian"
    assert _route_worker(["summarize"]) == "dicksimnel"


def test_routing_table_has_all_three_workers():
    workers = set(ROUTING_TABLE.values())
    assert "claude" in workers
    assert "librarian" in workers
    assert "dicksimnel" in workers


# ── Integration: _write_tickets_to_queue applies routing ─────────────────────


def test_write_tickets_routes_by_tags():
    """_write_tickets_to_queue applies _route_worker; research→librarian, build→claude."""
    from devices.vetinari.device import _write_tickets_to_queue

    subtasks = [
        {"title": "Research the system", "description": "d", "tags": ["Research"], "size": "S"},
        {"title": "Build the endpoint", "description": "d", "tags": ["Build"], "size": "M"},
    ]

    written_json = []

    def fake_run(cmd, **kwargs):
        # Capture the temp JSON file contents before it's deleted
        import json as _json
        tmp_path = cmd[-1]  # last arg is the temp file
        try:
            written_json.extend(_json.loads(open(tmp_path).read()))
        except Exception:
            pass
        m = patch("subprocess.CompletedProcess")
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    from unittest.mock import MagicMock
    with patch("devices.vetinari.device.subprocess.run", side_effect=fake_run):
        ids = _write_tickets_to_queue(subtasks, decision_id="d-routing")

    assert len(ids) == 2
    research = next(t for t in written_json if "Research" in t.get("tags", []))
    build = next(t for t in written_json if "Build" in t.get("tags", []))
    assert research["worker"] == "librarian"
    assert build["worker"] == "claude"
