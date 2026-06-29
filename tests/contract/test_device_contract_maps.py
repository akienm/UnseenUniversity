"""Contract: every architecture intention-point declares owns/delegates_to.

T-device-contract-schema (D-device-contract-maps-2026-06-29). The architecture
intention-points (devlab/runtime/memory/architecture/*.json) carry a per-device
CONTRACT: `owns` (capabilities this device holds) and `delegates_to` (map of
capability -> owning device). The map is intent, not a scrape of current code —
the whole point is to make a violation (e.g. igor's cognition owning inference)
detectable against a declared boundary.

Invariants pinned here (the test plan from the ticket):
  1. every intention-point has a non-empty `owns`.
  2. igor's intention-point (cognition): `inference` absent from owns, present in
     delegates_to -> the inference device.
  3. capability vocabulary is consistent: every `delegates_to` KEY appears
     verbatim in the OWNS of the device it names (for owners that have an
     intention-point file; `postgres`/`granny` have none and are whitelisted).

Plus the schema shape (refined with Akien 2026-06-29): `delegates_to` is present
on every file but may legitimately be EMPTY for the floor tier — the device/shim
CONTRACT itself and the flat-file dev-artifact store own their floor and delegate
no infra capability. Forcing a false entry there to satisfy a grep is the kind of
hollow build the proof-on-close gate exists to stop, so the test exempts exactly
those two and requires non-empty on the other 13 runtime devices.
"""
import json
from pathlib import Path

import pytest

from unseen_university._uu_root import uu_root

ARCH_DIR = Path(uu_root()) / "devlab" / "runtime" / "memory" / "architecture"

# Floor tier: a contract spec and a passive flat-file store — own their floor,
# delegate nothing. delegates_to is present but empty for these two by design.
FLOOR_TIER = {"device-shim", "memory-dev-artifacts"}

# Capability owners that have no intention-point file of their own (so the
# consistency check can't read their `owns`). Whitelisted as valid delegate
# targets.
OWNERS_WITHOUT_FILE = {"postgres", "granny"}


def _load():
    """subsystem -> body, for every architecture intention-point."""
    out = {}
    for f in sorted(ARCH_DIR.glob("*.json")):
        body = json.loads(f.read_text())["body"]
        out[body["subsystem"]] = body
    return out


@pytest.fixture(scope="module")
def bodies():
    b = _load()
    assert b, f"no architecture intention-points found under {ARCH_DIR}"
    return b


def test_every_point_has_nonempty_owns(bodies):
    """Test plan #1 (owns half): every intention-point owns something."""
    missing = [s for s, b in bodies.items() if not b.get("owns")]
    assert not missing, f"intention-points with empty/absent owns: {missing}"


def test_delegates_to_present_everywhere(bodies):
    """Schema shape: delegates_to KEY exists on every file (may be empty)."""
    missing = [s for s, b in bodies.items() if "delegates_to" not in b]
    assert not missing, f"intention-points missing delegates_to key: {missing}"


def test_runtime_devices_delegate_something(bodies):
    """Test plan #1 (delegates half): every runtime device (all but the floor
    tier) declares at least one delegated capability."""
    empty = [
        s
        for s, b in bodies.items()
        if s not in FLOOR_TIER and not b.get("delegates_to")
    ]
    assert not empty, f"runtime devices with empty delegates_to: {empty}"


def test_floor_tier_delegates_nothing(bodies):
    """The contract spec and the flat-file store delegate nothing — explicit
    empty, never a fudged entry."""
    for s in FLOOR_TIER:
        assert s in bodies, f"floor-tier subsystem {s} not found"
        assert bodies[s].get("delegates_to") == {}, (
            f"{s} is floor tier and must declare delegates_to == {{}}, "
            f"got {bodies[s].get('delegates_to')!r}"
        )


def test_igor_delegates_inference_not_owns_it(bodies):
    """Test plan #2: cognition (igor) does NOT own inference; it delegates it to
    the inference device."""
    cog = bodies["cognition"]
    # .get() (not []) so the pre-implementation tree yields an AssertionError
    # (authentic red for proof_emitter), not a KeyError (collateral, rejected).
    assert "inference" not in cog.get("owns", []), (
        "cognition must not OWN inference — it is igor uses-not-contains"
    )
    assert cog.get("delegates_to", {}).get("inference") == "inference", (
        "cognition must delegate inference -> inference device, "
        f"got {cog.get('delegates_to', {}).get('inference')!r}"
    )


def test_capability_vocabulary_is_consistent(bodies):
    """Test plan #3: one term per capability. Every delegates_to KEY must appear
    verbatim in the OWNS of the device it points to (for owners with a file)."""
    owns_by_subsystem = {s: set(b.get("owns", [])) for s, b in bodies.items()}
    violations = []
    for s, b in bodies.items():
        for cap, owner in (b.get("delegates_to") or {}).items():
            if owner in OWNERS_WITHOUT_FILE:
                continue
            if owner not in owns_by_subsystem:
                violations.append(
                    f"{s}.delegates_to[{cap!r}] -> unknown owner {owner!r} "
                    f"(no intention-point and not whitelisted)"
                )
            elif cap not in owns_by_subsystem[owner]:
                violations.append(
                    f"{s} delegates {cap!r} to {owner!r}, but {owner!r}.owns "
                    f"does not contain {cap!r} (vocabulary drift)"
                )
    assert not violations, "capability vocabulary inconsistencies:\n" + "\n".join(
        violations
    )
