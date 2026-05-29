#!/usr/bin/env python3
"""
wiring_check.py — T-audit-wiring-check

Verify that gated features (IGOR_*_ENABLED switches set to true) have
end-to-end wiring. Born from two incidents on 2026-04-16b:
  1. IGOR_TURN_PIPELINE=true caused stub VoiceProducer output
  2. IGOR_CALVING_ENABLED=true calved CP nodes from ROOT, crashing Igor

For each enabled switch, checks:
  - The env var is referenced in at least one .py file
  - The gated code path exists (function/method is not empty)
  - No obvious stub markers (TODO, FIXME, placeholder, stub, NotImplemented)

Usage:
    python3 lab/claudecode/wiring_check.py [--cfg PATH] [--source PATH]

Exit code: 0 = all OK, 1 = issues found
"""

import argparse
import os
import re
import sys
from pathlib import Path


def _load_switches(cfg_path: Path) -> dict[str, str]:
    """Load switch=value pairs from igor.switches.cfg."""
    switches = {}
    if not cfg_path.exists():
        return switches
    for line in cfg_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, val = line.split("=", 1)
            switches[key.strip()] = val.strip()
    return switches


def _find_enabled_switches(switches: dict[str, str]) -> list[str]:
    """Return switch names that are set to true/enabled."""
    enabled = []
    for key, val in switches.items():
        if val.lower() in ("true", "1", "yes"):
            enabled.append(key)
    return sorted(enabled)


def _find_references(switch_name: str, source_dir: Path) -> list[str]:
    """Find .py files that reference this switch name."""
    refs = []
    for py_file in source_dir.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue
        try:
            text = py_file.read_text(errors="ignore")
            if switch_name in text:
                refs.append(str(py_file))
        except Exception:
            pass
    return refs


_STUB_PATTERNS = [
    re.compile(r"#\s*TODO:", re.IGNORECASE),
    re.compile(r"#\s*FIXME:", re.IGNORECASE),
    re.compile(r"#\s*STUB", re.IGNORECASE),
    re.compile(r"#\s*PLACEHOLDER", re.IGNORECASE),
    re.compile(r"NotImplementedError"),
    re.compile(r"raise\s+NotImplementedError"),
    re.compile(r'return\s+"stub'),
    re.compile(r'return\s+"placeholder'),
    re.compile(r"pass\s*$"),
]


def _check_stubs_near_gate(switch_name: str, filepath: str) -> list[str]:
    """Check for stub markers within 20 lines of the switch reference."""
    issues = []
    try:
        lines = Path(filepath).read_text().splitlines()
    except Exception:
        return issues

    for i, line in enumerate(lines):
        if switch_name not in line:
            continue
        # Check 20 lines after the gate
        window = lines[i : i + 20]
        for j, wline in enumerate(window):
            for pat in _STUB_PATTERNS:
                if pat.search(wline):
                    # "pass" alone is OK if it's in an except block
                    if "pass" in wline and j > 0 and "except" in window[j - 1]:
                        continue
                    issues.append(f"  {filepath}:{i + j + 1} — {wline.strip()}")
    return issues


def run_wiring_check(
    cfg_path: Path | None = None,
    source_dir: Path | None = None,
) -> tuple[list[str], list[str]]:
    """
    Run the wiring check. Returns (ok_list, issue_list).
    Each entry is a human-readable string.
    """
    if cfg_path is None:
        runtime = Path(
            os.environ.get("IGOR_RUNTIME_ROOT", Path.home() / ".unseen_university")
        )
        instance_id = os.environ.get("IGOR_INSTANCE_ID", "Igor-wild-0001")
        cfg_path = runtime / instance_id / "igor.switches.cfg"

    if source_dir is None:
        source_dir = Path(__file__).parent.parent.parent / "devices" / "igor"

    switches = _load_switches(cfg_path)
    enabled = _find_enabled_switches(switches)

    ok = []
    issues = []

    if not enabled:
        ok.append("No enabled switches found")
        return ok, issues

    for switch in enabled:
        refs = _find_references(switch, source_dir)
        if not refs:
            issues.append(f"UNREFERENCED: {switch}=true but not found in source")
            continue

        # Check for stubs near the gate
        stubs = []
        for ref in refs:
            stubs.extend(_check_stubs_near_gate(switch, ref))

        if stubs:
            issues.append(f"STUB_NEAR_GATE: {switch}")
            issues.extend(stubs)
        else:
            ok.append(f"OK: {switch} — {len(refs)} reference(s), no stubs")

    return ok, issues


def main():
    parser = argparse.ArgumentParser(description="Check gated feature wiring")
    parser.add_argument("--cfg", type=Path, help="Path to igor.switches.cfg")
    parser.add_argument("--source", type=Path, help="Path to source directory")
    args = parser.parse_args()

    ok, issues = run_wiring_check(args.cfg, args.source)

    print("WIRING CHECK")
    print("=" * 60)
    for line in ok:
        print(f"  ✅ {line}")
    for line in issues:
        if line.startswith("  "):
            print(f"    {line.strip()}")
        else:
            print(f"  ⚠️  {line}")

    print(f"\n{len(ok)} OK, {len(issues)} issue(s)")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
