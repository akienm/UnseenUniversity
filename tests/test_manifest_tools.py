"""Tests for datacenter_manifest routing tool."""

from __future__ import annotations

import json

import pytest

from unseen_university.devices.librarian.tools.manifest_tools import (
    _ROUTING_MAP,
    datacenter_manifest,
    dispatch,
)


def test_routing_map_has_required_shapes():
    for shape in (
        "db_query",
        "db_write",
        "palace_read",
        "palace_search",
        "channel_read",
    ):
        assert shape in _ROUTING_MAP, f"missing shape: {shape}"


def test_each_entry_has_tool_and_when():
    for shape, entry in _ROUTING_MAP.items():
        assert "tool" in entry, f"{shape}: missing 'tool'"
        assert "when" in entry, f"{shape}: missing 'when'"
        assert entry["tool"], f"{shape}: 'tool' is empty"
        assert entry["when"], f"{shape}: 'when' is empty"


def test_routing_only_returns_routing_map_only():
    result = json.loads(datacenter_manifest(routing_only=True))
    assert "routing_map" in result
    assert "tools" not in result


def test_full_manifest_includes_tool_list():
    result = json.loads(datacenter_manifest(routing_only=False))
    assert "routing_map" in result
    assert "tools" in result
    assert isinstance(result["tools"], list)
    assert len(result["tools"]) > 0
    assert "datacenter_manifest" not in result["tools"]


def test_task_shape_lookup_known():
    result = json.loads(datacenter_manifest(task_shape="db_query"))
    assert result["task_shape"] == "db_query"
    assert result["routing"] is not None
    assert result["routing"]["tool"] == "db_query"


def test_task_shape_lookup_unknown_returns_known_shapes():
    result = json.loads(datacenter_manifest(task_shape="nonexistent_shape"))
    assert result["routing"] is None
    assert "known_shapes" in result
    assert "db_query" in result["known_shapes"]


def test_dispatch_routes_datacenter_manifest():
    result = dispatch("datacenter_manifest", {"routing_only": True})
    assert result is not None
    parsed = json.loads(result)
    assert "routing_map" in parsed


def test_dispatch_unknown_returns_none():
    assert dispatch("unknown_tool", {}) is None
