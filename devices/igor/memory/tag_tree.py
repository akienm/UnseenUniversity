"""tag_tree.py — Memory tag convention: metadata['tag:<path>'] marks a tag.

Akien's framing (2026-04-22): "to put tags on memory, just put them in the
metadata. an extra level of linked relationships. and the collection of tags
itself can become a tree just from having tag:<name> in the metadata."

No new table, no new memory type. Any key in a memory's metadata JSONB that
starts with 'tag:' marks that memory as tagged. Slash-separated paths
(tag:pe_chain/situate) produce a nested tree when assembled across memories.

This module holds the pure helpers (no DB). DB-backed methods live on Cortex:
apply_tag, get_tags_for, memories_with_tag, tag_tree.
"""

from __future__ import annotations

from typing import Iterable

TAG_PREFIX = "tag:"


def extract_tag_names(metadata: dict | None) -> list[str]:
    """Return tag names (prefix stripped) from a metadata dict.

    Non-dict input returns []. Tags are sorted for stable output.
    """
    if not isinstance(metadata, dict):
        return []
    return sorted(
        k[len(TAG_PREFIX) :]
        for k in metadata.keys()
        if isinstance(k, str) and k.startswith(TAG_PREFIX) and len(k) > len(TAG_PREFIX)
    )


def build_tag_tree(tag_lists: Iterable[list[str]]) -> dict:
    """Assemble a nested tag tree from per-memory tag lists.

    Each leaf node carries a `_count` of how many memories carry that exact tag
    (including any deeper descendants — counts aggregate up the tree). Slash-
    separated tag names create nested structure:

        tag:a, tag:a/b, tag:a/b/c
          → {'a': {'_count': 3, 'b': {'_count': 2, 'c': {'_count': 1}}}}

    Tags without slashes appear as leaves at the top level.
    """
    tree: dict = {}
    for tags in tag_lists:
        for tag in tags:
            parts = [p for p in tag.split("/") if p]
            if not parts:
                continue
            node = tree
            for part in parts:
                node = node.setdefault(part, {})
                node["_count"] = node.get("_count", 0) + 1
    return tree
