"""CP1 boundary proof — every device and shim structurally carries CP1–CP6.

This test IS the teeth of the "values in the base" contract: it is not enough to
*place* the values, the test must fail if any device could lack them. See
D-per-project-split-and-contracts-2026-06-20. Promoting the values without this
test is exactly the confabulated-"done" that this work is correcting.

Kept import-light on purpose (no DB, no device boot) so it runs in the targeted
day-to-day suite, not just the overnight run.
"""

from __future__ import annotations

from pathlib import Path

from unseen_university.diagnostic_base.core_values import CORE_VALUES, CoreValue, CoreValuesMixin
from unseen_university.device import BaseDevice
from unseen_university.shim import BaseShim

EXPECTED_IDS = ["CP1", "CP2", "CP3", "CP4", "CP5", "CP6"]
REPO_ROOT = Path(__file__).resolve().parents[1]


def _all_subclasses(cls: type) -> set[type]:
    seen: set[type] = set()
    stack = list(cls.__subclasses__())
    while stack:
        sub = stack.pop()
        if sub in seen:
            continue
        seen.add(sub)
        stack.extend(sub.__subclasses__())
    return seen


def test_canonical_values_are_the_six_in_order():
    assert [v.id for v in CORE_VALUES] == EXPECTED_IDS
    for v in CORE_VALUES:
        assert isinstance(v, CoreValue)
        assert v.narrative.strip(), f"{v.id} missing narrative"
        assert v.why.strip(), f"{v.id} missing why (CP3: there's always a why)"


def test_base_device_and_shim_inherit_the_mixin():
    # The structural guarantee: because both bases compose CoreValuesMixin, every
    # device and every shim inherits all six by Python's own semantics.
    for base in (BaseDevice, BaseShim):
        assert issubclass(base, CoreValuesMixin), (
            f"{base.__name__} must compose CoreValuesMixin — without it, devices "
            f"can exist that are not driven by the core values"
        )
        assert [v.id for v in base.CORE_VALUES] == EXPECTED_IDS


def test_no_imported_device_or_shim_lacks_the_values():
    # Catches a future class that overrides CORE_VALUES to something divergent, or
    # a regression that drops the mixin. Covers every BaseDevice/BaseShim subclass
    # currently imported into the interpreter.
    offenders = []
    for base in (BaseDevice, BaseShim):
        for cls in _all_subclasses(base):
            ids = [getattr(v, "id", None) for v in getattr(cls, "CORE_VALUES", [])]
            if ids != EXPECTED_IDS:
                offenders.append(f"{cls.__module__}.{cls.__name__} -> {ids}")
    assert not offenders, "device/shim classes lacking core values: " + "; ".join(offenders)


def test_core_value_lookup_helper():
    assert CoreValuesMixin.core_value("CP1").narrative == "I don't know"
    try:
        CoreValuesMixin.core_value("CP99")
    except KeyError:
        pass
    else:  # pragma: no cover
        raise AssertionError("core_value should raise KeyError for unknown id")


def test_igor_brainstem_does_not_duplicate_the_values():
    # DRY / single source of truth: the brainstem must source from canonical, not
    # re-inline the literals (that duplication is what drifts).
    src = (
        REPO_ROOT / "unseen_university/devices/igor/brainstem/core_patterns.py"
    ).read_text()
    assert "from unseen_university.diagnostic_base.core_values import CORE_VALUES" in src
    assert "core_patterns = [" not in src, (
        "brainstem re-inlined the core-value literals — import CORE_VALUES instead"
    )
