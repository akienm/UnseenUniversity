"""
profile.py — load and validate agent capability profiles.

Profiles are static YAML declarations that describe what an agent type
can bind to. They live at:
  canonical:  unseen_university/config/profiles/<agent_id>.yaml  (repo)
  runtime:    ~/.unseen_university/profiles/<agent_id>.yaml      (install target)

The broker reads from the runtime directory; tests inject profiles_dir.

Inheritance (slice 5): a profile can declare inherits: [parent1, parent2].
Parents are loaded recursively, deep-merged left-to-right, then the
child's keys are layered on top. Lists in the child replace lists in
parents (no implicit union — too magical). To explicitly drop or
override a parent's value, use the {__replace__: True, value: ...}
sentinel — the value field becomes the merged result.

Note on lineage aliases: NOT present in this module by design. The
E-decision (§ 14, 2026-05-01) resolved to ship clean — no backwards-compat
alias path for comms://igor-wild-0001. Single user, research project. If
the lineage form breaks anything, we fix it then.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

log = logging.getLogger(__name__)

try:
    import yaml  # type: ignore[import]
except ImportError as exc:  # pragma: no cover
    raise ImportError("PyYAML required: pip install pyyaml") from exc

# Default runtime profiles directory — can be overridden per-call.
DEFAULT_PROFILES_DIR = Path("~/.unseen_university/profiles").expanduser()

# Top-level keys the profile loader understands; extra keys are preserved
# but logged at DEBUG so callers can detect typos.
_KNOWN_KEYS = frozenset(
    {
        "profile_version",
        "agent_type",
        "description",
        "inherits",
        "allowed_devices",
        "device_permissions",
        "default_channels",
        "state_refs",
        "acl",
        "surfaces",
        "visibility",
    }
)


class ProfileNotFoundError(Exception):
    """Raised when no profile YAML exists for the requested agent_id."""


class ProfileValidationError(ValueError):
    """Raised when a profile YAML is missing required fields or is malformed."""


def load_profile(
    agent_id: str,
    profiles_dir: Path | str | None = None,
    _seen: tuple[str, ...] = (),
) -> dict:
    """
    Load profile YAML for agent_id from profiles_dir (default: runtime dir).

    Resolves inherits chains recursively (slice 5): parents merge
    left-to-right, child layers on top. Dicts deep-merge; lists in the
    child replace lists in parents; {__replace__: True, value: X} forces
    a wholesale replacement.

    Raises ProfileNotFoundError if the file (or any parent) is absent.
    Raises ProfileValidationError if required fields are missing or a
    cycle is detected in the inherits graph.
    """
    if agent_id in _seen:
        cycle = " → ".join(_seen + (agent_id,))
        raise ProfileValidationError(f"Profile inheritance cycle detected: {cycle}")

    d = Path(profiles_dir) if profiles_dir is not None else DEFAULT_PROFILES_DIR
    path = d / f"{agent_id}.yaml"
    if not path.exists():
        raise ProfileNotFoundError(
            f"No profile for agent_id={agent_id!r} — expected {path}"
        )

    text = path.read_text(encoding="utf-8")
    profile = yaml.safe_load(text)

    if not isinstance(profile, dict):
        raise ProfileValidationError(f"Profile for {agent_id!r} is not a YAML mapping")

    _validate(agent_id, profile)

    unknown = set(profile.keys()) - _KNOWN_KEYS
    if unknown:
        log.debug("Profile %r has unrecognised keys: %s", agent_id, unknown)

    inherits = profile.get("inherits", []) or []
    if not inherits:
        return _resolve_replace_markers(profile)

    merged: dict = {}
    next_seen = _seen + (agent_id,)
    for parent_id in inherits:
        parent_profile = load_profile(
            parent_id, profiles_dir=profiles_dir, _seen=next_seen
        )
        merged = _deep_merge(merged, parent_profile)

    final = _deep_merge(merged, profile)
    # The child's own inherits list is metadata for the loader, not for
    # downstream consumers — strip it from the resolved profile.
    final["inherits"] = []
    return _resolve_replace_markers(final)


def profile_yaml_etag(agent_id: str, profiles_dir: Path | str | None = None) -> str:
    """SHA-256 of the raw YAML text — used as cache key in Manifest."""
    d = Path(profiles_dir) if profiles_dir is not None else DEFAULT_PROFILES_DIR
    path = d / f"{agent_id}.yaml"
    if not path.exists():
        raise ProfileNotFoundError(f"No profile for {agent_id!r} at {path}")
    text = path.read_text(encoding="utf-8")
    return hashlib.sha256(text.encode()).hexdigest()


def _validate(agent_id: str, profile: dict) -> None:
    required = {"profile_version", "agent_type"}
    missing = required - profile.keys()
    if missing:
        raise ProfileValidationError(
            f"Profile for {agent_id!r} missing required fields: {missing}"
        )


_REPLACE_MARKER = "__replace__"


def _is_replace_marker(value) -> bool:
    return (
        isinstance(value, dict)
        and value.get(_REPLACE_MARKER) is True
        and "value" in value
    )


def _deep_merge(base: dict, overlay: dict) -> dict:
    """
    Recursively merge overlay onto base. Behavior:
      - dicts merge key-by-key, recursing into nested dicts
      - lists in overlay REPLACE lists in base (no implicit union)
      - scalars in overlay replace scalars in base
      - {__replace__: True, value: X} in overlay replaces wholesale —
        the marker is unwrapped by _resolve_replace_markers at the end.
    Returns a new dict; inputs are not mutated.
    """
    result: dict = dict(base)
    for key, overlay_val in overlay.items():
        if _is_replace_marker(overlay_val):
            result[key] = overlay_val  # unwrap later in _resolve_replace_markers
            continue
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(overlay_val, dict)
            and not _is_replace_marker(result[key])
        ):
            result[key] = _deep_merge(result[key], overlay_val)
        else:
            result[key] = overlay_val
    return result


def _resolve_replace_markers(profile: dict) -> dict:
    """Walk the resolved profile and unwrap any remaining __replace__ markers."""
    result: dict = {}
    for key, value in profile.items():
        if _is_replace_marker(value):
            result[key] = value["value"]
        elif isinstance(value, dict):
            result[key] = _resolve_replace_markers(value)
        else:
            result[key] = value
    return result
