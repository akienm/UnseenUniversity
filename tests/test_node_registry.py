"""Tests for the compiled-inference node registry."""

from __future__ import annotations

import pytest

from devices.librarian.node_registry import (
    NODE_REGISTRY,
    get_canonical_query,
    get_fts_targets,
    get_node_types,
    validate_registry,
)

EXPECTED_NODE_TYPES = {"ticket", "memory", "channel_message", "palace_node", "reading_item", "eval_result"}
REQUIRED_KEYS = {"table", "tags_column", "fts_columns", "canonical_query"}


def test_get_canonical_query_returns_sql_for_known_type():
    sql = get_canonical_query("ticket")
    assert sql is not None
    assert "clan.memories" in sql
    assert "%s::jsonb" in sql


def test_get_canonical_query_returns_none_for_unknown_type():
    assert get_canonical_query("nonexistent_type") is None


def test_get_node_types_returns_all_expected():
    types = set(get_node_types())
    assert EXPECTED_NODE_TYPES <= types


def test_get_fts_targets_covers_all_node_types():
    targets = get_fts_targets()
    target_types = {t["node_type"] for t in targets}
    assert EXPECTED_NODE_TYPES <= target_types


def test_all_entries_have_required_keys():
    for node_type, entry in NODE_REGISTRY.items():
        missing = REQUIRED_KEYS - set(entry.keys())
        assert not missing, f"{node_type} missing keys: {missing}"


def test_fts_columns_are_lists():
    for node_type, entry in NODE_REGISTRY.items():
        assert isinstance(entry["fts_columns"], list), f"{node_type}.fts_columns must be list"
        assert len(entry["fts_columns"]) >= 1, f"{node_type}.fts_columns must be non-empty"


def test_canonical_queries_are_parameterized():
    for node_type, entry in NODE_REGISTRY.items():
        assert "%s" in entry["canonical_query"], f"{node_type} canonical_query must use %s placeholder"


def test_validate_registry_returns_empty_for_well_formed_registry():
    errors = validate_registry()
    assert errors == [], f"Registry has validation errors: {errors}"


def test_channel_message_query_references_correct_table():
    sql = get_canonical_query("channel_message")
    assert "infra.channel_messages" in sql


def test_palace_node_query_references_correct_table():
    sql = get_canonical_query("palace_node")
    assert "adc.palace" in sql


def test_fts_target_has_expected_shape():
    targets = get_fts_targets()
    for t in targets:
        assert "node_type" in t
        assert "table" in t
        assert "columns" in t
        assert isinstance(t["columns"], list)
        assert "filter_sql" in t  # may be None


def test_ticket_filter_distinguishes_from_memory():
    ticket_entry = NODE_REGISTRY["ticket"]
    memory_entry = NODE_REGISTRY["memory"]
    # Both point to clan.memories but with different filters
    assert ticket_entry["table"] == memory_entry["table"]
    assert ticket_entry["filter_sql"] != memory_entry["filter_sql"]
