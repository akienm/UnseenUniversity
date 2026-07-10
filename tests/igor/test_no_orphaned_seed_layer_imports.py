"""
Guard: no test module imports a retired ``claudecode.seed_layer*`` seed template.

The layer3/layer4 seed templates (``seed_layer3_*.py`` / ``seed_layer4_*.py``, 13
modules) were deleted as dead retired scripts in commit 3fcfd070 ("delete dead
retired scripts"). Nine test modules that imported them were left behind and
errored at collection with ModuleNotFoundError (T-code-indexer... no —
T-fix-igor-layer3-4-test-seed-imports). Those orphans were removed; this guard
keeps the class from silently recurring — the next time a subsystem is retired
without its tests, this fails at the import site instead of rotting as a
permanent collection error nobody reads.

Falsifiable: restore any orphaned test that imports ``claudecode.seed_layer*``
and this fails with AssertionError, naming the offender.
"""

from __future__ import annotations

import re
from pathlib import Path

# Match a real import line for the retired seed subsystem, e.g.
#   from claudecode.seed_layer3_constrain import ...
#   import claudecode.seed_layer4_run_bash
_RETIRED = re.compile(r"^\s*(from|import)\s+claudecode\.seed_layer", re.MULTILINE)

_TESTS_ROOT = Path(__file__).resolve().parent.parent  # tests/
_SELF = Path(__file__).resolve()


def test_no_test_imports_retired_seed_layer_module():
    offenders: list[str] = []
    for py in _TESTS_ROOT.rglob("*.py"):
        if py.resolve() == _SELF:
            continue  # this guard names the pattern; don't flag itself
        text = py.read_text(encoding="utf-8", errors="ignore")
        if _RETIRED.search(text):
            offenders.append(str(py.relative_to(_TESTS_ROOT.parent)))

    assert not offenders, (
        "Test modules import retired claudecode.seed_layer* modules "
        f"(deleted in 3fcfd070) and will error at collection: {sorted(offenders)}"
    )
