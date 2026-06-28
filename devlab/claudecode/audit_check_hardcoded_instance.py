"""
audit_check_hardcoded_instance.py — T-hardcoded-instance-refs audit check

Fails if Igor's runtime code (unseen_university/devices/igor/) reintroduces hardcoded
'Igor-wild-0001' strings or the placeholder DB password outside the
known-exempt files: paths.py (canonical default), cluster_ssh.py (ssh user
constant + windows user env default), cognition/{job_manager,response_habituation,
pipeline_manager,machine_manager}.py (docstrings), network/channels/file_inbox.py
(docstring), memory/node_id.py (default instance constant), main.py (user-facing
help text + instance_id echo), tools/{notebook,cluster_ssh,google_calendar,
ebook_reader}.py (docstrings), arbiter/queue.py (docstring), setup_assets/
installer.py (bootstrap before config exists), config.cfg.template (documentation
template).

Exit 0: clean (no unexpected matches).
Exit 1: dirty (print violations).

The exempt list is the settled baseline. New matches mean a regression —
either something re-hardcoded or a new file needs an exempt-list update.
"""

import subprocess
import sys
from pathlib import Path

EXEMPT_SUFFIXES: set[str] = {
    "devices/igor/paths.py",
    "devices/igor/cognition/job_manager.py",
    "devices/igor/cognition/response_habituation.py",
    "devices/igor/cognition/pipeline_manager.py",
    "devices/igor/cognition/machine_manager.py",
    "devices/igor/network/channels/file_inbox.py",
    "devices/igor/main.py",
    "devices/igor/memory/node_id.py",
    "devices/igor/tools/reading_tool.py",  # worker-script template path, exempt
    "devices/igor/tools/notebook.py",
    "devices/igor/tools/cluster_ssh.py",
    "devices/igor/tools/google_calendar.py",
    "devices/igor/tools/ebook_reader.py",
    "devices/igor/arbiter/queue.py",
    "devices/igor/config.py",  # defines the os.getenv default — canonical source
    "devices/igor/env_sync.py",  # boot-time env hydration helper — same default-fallback pattern as config.py
    "devices/igor/setup_assets/installer.py",  # bootstrap launcher before config exists; paths() unavailable
    "devices/igor/config.cfg.template",  # documentation template showing placeholder defaults for users
}

PATTERNS: list[str] = [
    "Igor-wild-0001",
    "choose_a" "_password",  # fragmented: detector needle, literal must not live in source
]


def main() -> int:
    repo = Path(__file__).resolve().parents[2]
    src = repo / "unseen_university" / "devices" / "igor"

    if not src.exists():
        print(f"AUDIT ERROR: source tree not found at {src}")
        return 2

    try:
        result = subprocess.run(
            [
                "grep",
                "-rn",
                "-E",
                "|".join(PATTERNS),
                str(src),
            ],
            capture_output=True,
            text=True,
            timeout=20,
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
        # Extract path from "path:lineno:content"
        try:
            path_part = line.split(":", 1)[0]
        except Exception:
            continue
        path_obj = Path(path_part)
        # Test whether this file is exempt by suffix match
        rel = str(path_obj.relative_to(repo)) if path_obj.is_absolute() else path_part
        if any(rel.endswith(suffix) for suffix in EXEMPT_SUFFIXES):
            continue
        violations.append(line)

    if violations:
        print(
            f"FAIL: {len(violations)} hardcoded instance refs found outside exempt list:"
        )
        for v in violations:
            print(f"  {v}")
        print()
        print("Exempt suffixes:")
        for s in sorted(EXEMPT_SUFFIXES):
            print(f"  {s}")
        return 1

    print(
        f"PASS: hardcoded instance refs audit clean "
        f"(exempt list: {len(EXEMPT_SUFFIXES)} files)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
