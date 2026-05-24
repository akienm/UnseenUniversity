"""
Profile inheritance tests (slice 5).

Verifies load_profile() with non-empty inherits chains:
  - dicts deep-merge
  - lists in child replace lists in parents (no implicit union)
  - {__replace__: True, value: X} forces wholesale replacement
  - left-to-right resolution of multi-parent inherits
  - cycle detection raises ProfileValidationError
  - missing parent raises ProfileNotFoundError
"""

from __future__ import annotations

from pathlib import Path

import pytest

from unseen_university.announce.profile import (
    ProfileNotFoundError,
    ProfileValidationError,
    load_profile,
)


def _write(tmp_path: Path, name: str, body: str) -> None:
    (tmp_path / f"{name}.yaml").write_text(body)


# ── Baseline: no inherits unchanged ───────────────────────────────────────────


def test_no_inherits_unchanged(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "leaf",
        """
profile_version: "1.0"
agent_type: leaf
inherits: []
allowed_devices: [a, b]
""",
    )
    profile = load_profile("leaf", profiles_dir=tmp_path)
    assert profile["agent_type"] == "leaf"
    assert profile["allowed_devices"] == ["a", "b"]


# ── Single-parent merge ───────────────────────────────────────────────────────


def test_single_parent_merge_child_overrides_scalar(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "base",
        """
profile_version: "1.0"
agent_type: base
description: "parent description"
""",
    )
    _write(
        tmp_path,
        "child",
        """
profile_version: "1.0"
agent_type: child
description: "child overrides"
inherits: [base]
""",
    )
    profile = load_profile("child", profiles_dir=tmp_path)
    assert profile["description"] == "child overrides"
    assert profile["agent_type"] == "child"


def test_dict_deep_merge(tmp_path: Path) -> None:
    """device_permissions in parent + child merge key-by-key."""
    _write(
        tmp_path,
        "base",
        """
profile_version: "1.0"
agent_type: base
device_permissions:
  inference:
    mode: read_only
    rate_limit_per_min: 10
  postgres:
    mode: read_write
""",
    )
    _write(
        tmp_path,
        "child",
        """
profile_version: "1.0"
agent_type: child
inherits: [base]
device_permissions:
  inference:
    mode: read_write   # overrides parent
  swadl:
    mode: read_write   # added
""",
    )
    profile = load_profile("child", profiles_dir=tmp_path)
    perms = profile["device_permissions"]
    assert perms["inference"]["mode"] == "read_write"
    assert perms["inference"]["rate_limit_per_min"] == 10  # preserved from parent
    assert perms["postgres"]["mode"] == "read_write"  # untouched parent key
    assert perms["swadl"]["mode"] == "read_write"  # added in child


# ── List replacement ─────────────────────────────────────────────────────────


def test_list_child_replaces_parent(tmp_path: Path) -> None:
    """allowed_devices in child REPLACES the parent's list (no implicit union)."""
    _write(
        tmp_path,
        "base",
        """
profile_version: "1.0"
agent_type: base
allowed_devices: [inference, postgres, browser_use]
""",
    )
    _write(
        tmp_path,
        "child",
        """
profile_version: "1.0"
agent_type: child
inherits: [base]
allowed_devices: [inference]   # narrower — wins outright
""",
    )
    profile = load_profile("child", profiles_dir=tmp_path)
    assert profile["allowed_devices"] == ["inference"]


# ── __replace__ marker ───────────────────────────────────────────────────────


def test_replace_marker_drops_parent_value(tmp_path: Path) -> None:
    """Child uses {__replace__: True, value: []} to clear a parent list."""
    _write(
        tmp_path,
        "base",
        """
profile_version: "1.0"
agent_type: base
default_channels: [shared, igor-cc]
""",
    )
    _write(
        tmp_path,
        "child",
        """
profile_version: "1.0"
agent_type: child
inherits: [base]
default_channels:
  __replace__: true
  value: []
""",
    )
    profile = load_profile("child", profiles_dir=tmp_path)
    assert profile["default_channels"] == []


def test_replace_marker_overrides_dict_with_scalar(tmp_path: Path) -> None:
    """__replace__ can replace a dict with a different shape entirely."""
    _write(
        tmp_path,
        "base",
        """
profile_version: "1.0"
agent_type: base
state_refs:
  twm: "postgres://...#twm"
  ne: "postgres://...#ne"
""",
    )
    _write(
        tmp_path,
        "child",
        """
profile_version: "1.0"
agent_type: child
inherits: [base]
state_refs:
  __replace__: true
  value: {}
""",
    )
    profile = load_profile("child", profiles_dir=tmp_path)
    assert profile["state_refs"] == {}


# ── Multi-parent left-to-right resolution ─────────────────────────────────────


def test_chain_resolution_left_to_right(tmp_path: Path) -> None:
    """When two parents define the same key, the rightmost wins."""
    _write(
        tmp_path,
        "left",
        """
profile_version: "1.0"
agent_type: left
description: "from left"
""",
    )
    _write(
        tmp_path,
        "right",
        """
profile_version: "1.0"
agent_type: right
description: "from right"
""",
    )
    _write(
        tmp_path,
        "child",
        """
profile_version: "1.0"
agent_type: child
inherits: [left, right]
""",
    )
    profile = load_profile("child", profiles_dir=tmp_path)
    assert profile["description"] == "from right"


def test_grandparent_chain(tmp_path: Path) -> None:
    """Recursion: child → parent → grandparent inherit chain."""
    _write(
        tmp_path,
        "grand",
        """
profile_version: "1.0"
agent_type: grand
allowed_devices: [a]
description: "grand"
""",
    )
    _write(
        tmp_path,
        "parent",
        """
profile_version: "1.0"
agent_type: parent
inherits: [grand]
description: "parent"
""",
    )
    _write(
        tmp_path,
        "child",
        """
profile_version: "1.0"
agent_type: child
inherits: [parent]
""",
    )
    profile = load_profile("child", profiles_dir=tmp_path)
    assert profile["allowed_devices"] == ["a"]  # all the way from grand
    assert profile["description"] == "parent"  # parent overrides grand


# ── Error paths ───────────────────────────────────────────────────────────────


def test_cycle_detection_raises(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "a",
        """
profile_version: "1.0"
agent_type: a
inherits: [b]
""",
    )
    _write(
        tmp_path,
        "b",
        """
profile_version: "1.0"
agent_type: b
inherits: [a]
""",
    )
    with pytest.raises(ProfileValidationError, match="cycle detected"):
        load_profile("a", profiles_dir=tmp_path)


def test_missing_parent_raises(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "child",
        """
profile_version: "1.0"
agent_type: child
inherits: [no-such-parent]
""",
    )
    with pytest.raises(ProfileNotFoundError):
        load_profile("child", profiles_dir=tmp_path)


def test_inherits_field_stripped_after_resolution(tmp_path: Path) -> None:
    """Once resolved, the merged profile's inherits field should be empty."""
    _write(
        tmp_path,
        "base",
        """
profile_version: "1.0"
agent_type: base
""",
    )
    _write(
        tmp_path,
        "child",
        """
profile_version: "1.0"
agent_type: child
inherits: [base]
""",
    )
    profile = load_profile("child", profiles_dir=tmp_path)
    assert profile["inherits"] == []
