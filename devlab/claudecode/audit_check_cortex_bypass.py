"""
audit_check_cortex_bypass.py — T-cortex-store-bypass-audit

Fails if devices/igor/tools/ contains a runtime file with `INSERT INTO
memories` that isn't a seed_*.py script or memory_sync.py (which are
legitimate genesis / sync bypass paths).

Seed scripts run at boot with known-clean genesis content; memory_sync
is the sync layer that routes around cortex by definition. Everything
else should go through cortex.store() per DP4 (all r/w through Cortex
only) so scrub, credential filtering, test_data stamping, and D256 id
handling all apply uniformly.

Exit 0: clean.
Exit 1: dirty (print violations).
"""

import subprocess
import sys
from pathlib import Path

EXEMPT_FILENAME_PREFIXES: tuple[str, ...] = ("seed_",)
EXEMPT_FILENAMES: set[str] = {
    "memory_sync.py",  # sync layer by definition
}


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    src = Path("/home/akien/dev/src/UnseenUniversity") / "devices" / "igor" / "tools"

    if not src.exists():
        print(f"AUDIT ERROR: tools tree not found at {src}")
        return 2

    try:
        result = subprocess.run(
            ["grep", "-rln", "INSERT INTO memories", str(src)],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:
        print(f"AUDIT ERROR: grep failed: {exc}")
        return 2

    if result.returncode not in (0, 1):
        print(f"AUDIT ERROR: grep exit {result.returncode}: {result.stderr}")
        return 2

    violations: list[str] = []
    for line in result.stdout.splitlines():
        if "__pycache__" in line:
            continue
        path = Path(line.strip())
        name = path.name
        if any(name.startswith(p) for p in EXEMPT_FILENAME_PREFIXES):
            continue
        if name in EXEMPT_FILENAMES:
            continue
        violations.append(str(path))

    if violations:
        print(f"FAIL: {len(violations)} runtime tool file(s) bypass cortex.store:")
        for v in violations:
            print(f"  {v}")
        print()
        print("Route writes through cortex.store() via a Memory object.")
        print("See T-goal-graph-use-cortex-store (commit 362f969e) for the pattern.")
        print()
        print("Legitimate exemptions:")
        for prefix in EXEMPT_FILENAME_PREFIXES:
            print(f"  {prefix}*.py (genesis/boot seeding)")
        for name in EXEMPT_FILENAMES:
            print(f"  {name}")
        return 1

    print(
        "PASS: no unexpected cortex.store bypasses in tools/ "
        f"(exempt: seed_*.py, {sorted(EXEMPT_FILENAMES)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
