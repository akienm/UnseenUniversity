"""
node_registry.py — Compiled-inference query registry for unified node types.

Maps each node_type to its canonical table, fast-path tags column, FTS columns,
and a parameterized canonical query for the most common access pattern.

This is the "compiled inference" layer: the system knows the access patterns at
design time and provides pre-built queries instead of ad-hoc searches.
T-unified-node-rollout.
"""

from __future__ import annotations

NODE_REGISTRY: dict[str, dict] = {
    "ticket": {
        "table": "clan.memories",
        "tags_column": "tags",
        "fts_columns": ["narrative"],
        "filter_sql": "metadata->>'kind' = 'ticket'",
        "canonical_query": (
            "SELECT id, narrative, tags, metadata, updated_at "
            "FROM clan.memories "
            "WHERE metadata->>'kind' = 'ticket' AND tags @> %s::jsonb "
            "ORDER BY updated_at DESC NULLS LAST"
        ),
    },
    "memory": {
        "table": "clan.memories",
        "tags_column": "tags",
        "fts_columns": ["narrative"],
        "filter_sql": "metadata->>'kind' IS DISTINCT FROM 'ticket'",
        "canonical_query": (
            "SELECT id, narrative, tags, metadata, updated_at "
            "FROM clan.memories "
            "WHERE metadata->>'kind' IS DISTINCT FROM 'ticket' AND tags @> %s::jsonb "
            "ORDER BY updated_at DESC NULLS LAST"
        ),
    },
    "channel_message": {
        "table": "infra.channel_messages",
        "tags_column": "tags",
        "fts_columns": ["content"],
        "filter_sql": None,
        "canonical_query": (
            "SELECT id::text, content, author, channel, tags, ts "
            "FROM infra.channel_messages "
            "WHERE tags @> %s::jsonb "
            "ORDER BY ts DESC NULLS LAST"
        ),
    },
    "palace_node": {
        "table": "adc.palace",
        "tags_column": "tags",
        "fts_columns": ["content", "title"],
        "filter_sql": None,
        "canonical_query": (
            "SELECT path, title, content, tags, updated_at "
            "FROM adc.palace "
            "WHERE tags @> %s::jsonb "
            "ORDER BY updated_at DESC NULLS LAST"
        ),
    },
    "reading_item": {
        "table": "clan.reading_list",
        "tags_column": "tags",
        "fts_columns": ["url", "title", "summary"],
        "filter_sql": None,
        "canonical_query": (
            "SELECT id, url, title, tags "
            "FROM clan.reading_list "
            "WHERE tags @> %s::jsonb"
        ),
    },
    "eval_result": {
        "table": "adc.eval_history",
        "tags_column": "tags",
        "fts_columns": ["output_text"],
        "filter_sql": None,
        "canonical_query": (
            "SELECT id, agent_id, rubric_id, score, verdict, tags, evaluated_at "
            "FROM adc.eval_history "
            "WHERE tags @> %s::jsonb "
            "ORDER BY evaluated_at DESC NULLS LAST"
        ),
    },
}

_REQUIRED_KEYS = {"table", "tags_column", "fts_columns", "canonical_query"}


def get_node_types() -> list[str]:
    """Return the list of registered node type names."""
    return list(NODE_REGISTRY.keys())


def get_canonical_query(node_type: str) -> str | None:
    """Return the pre-built parameterized SQL for the given node type, or None."""
    entry = NODE_REGISTRY.get(node_type)
    return entry["canonical_query"] if entry else None


def get_fts_targets() -> list[dict]:
    """Return FTS target specs for cross-type search — one dict per node type.

    Each dict has: node_type, table, columns (list[str]), filter_sql (str|None).
    Used by recall.py's cross-type FTS path.
    """
    return [
        {
            "node_type": k,
            "table": v["table"],
            "columns": v["fts_columns"],
            "filter_sql": v.get("filter_sql"),
        }
        for k, v in NODE_REGISTRY.items()
    ]


def validate_registry() -> list[str]:
    """Return a list of validation errors; empty list = all entries are well-formed."""
    errors = []
    for name, entry in NODE_REGISTRY.items():
        missing = _REQUIRED_KEYS - set(entry.keys())
        if missing:
            errors.append(f"{name}: missing keys {sorted(missing)}")
        if not isinstance(entry.get("fts_columns"), list):
            errors.append(f"{name}: fts_columns must be a list")
    return errors
