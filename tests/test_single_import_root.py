"""Proof for T-collapse-to-single-package (D-single-package-reorg-2026-06-28).

Intention: the codebase has one heart — a single import root under
unseen_university/ — so no CC/DS instance has to guess which of several trees is
canonical. These assertions would all FAIL on the pre-reorg tree (the moved
import paths did not exist; the old top-level packages did), so a hollow build
cannot pass them.
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_single_import_root_and_cold_start():
    # 1. The moved packages import under the single root (ImportError on pre-reorg tree).
    import unseen_university
    import unseen_university.devices.bus.router  # noqa: F401  (torn-merge: uu half)
    import unseen_university.devices.bus.connection  # noqa: F401  (torn-merge: root half)
    import unseen_university.devices.skeleton.skeleton  # noqa: F401
    import unseen_university.devices.skeleton.halt_registry  # noqa: F401
    import unseen_university.diagnostic_base  # noqa: F401
    from unseen_university.config.device_config import DeviceConfig  # noqa: F401

    # 2. Package __init__ files stay EMPTY/lazy (cold-start invariant).
    assert (REPO / "unseen_university" / "__init__.py").read_text().strip() == ""
    assert (REPO / "unseen_university" / "devices" / "__init__.py").read_text().strip() == ""

    # 3. Cold-start guard: importing skeleton in a FRESH interpreter pulls no psycopg2.
    #    (Subprocess so other tests' imports can't pollute sys.modules.)
    code = (
        "import sys; import unseen_university.devices.skeleton.skeleton, "
        "unseen_university.devices.skeleton.halt_registry; "
        "pg=[m for m in sys.modules if 'psycopg' in m.lower()]; "
        "sys.exit('COLD-START VIOLATION: '+repr(pg) if pg else 0)"
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr

    # 4. No straggler imports of the old top-level packages anywhere in the shipped package.
    import re
    straggler = re.compile(
        r"^[ \t]*(from|import)[ \t]+(bus|skeleton|devices|diagnostic_base|config)([. \t]|$)"
    )
    offenders = []
    for py in (REPO / "unseen_university").rglob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if straggler.match(line):
                offenders.append(f"{py.relative_to(REPO)}:{i}: {line.strip()}")
    assert not offenders, "old-top-level imports survive:\n" + "\n".join(offenders[:20])
