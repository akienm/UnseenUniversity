"""Path-moves monitor — day-close guard for the canonical memory store.

A renamed store with a surviving write-path silently split the source of truth
once (the misfiled decisions of 2026-06-21..23). This monitor makes that class of
drift loud: it runs the path-moves registry
(`devlab/runtime/memory/rules/path_moves.json`) against the git file index and
raises a system alarm for any dev-process artifact found under a retired path or
outside the canonical home.

Python-only, read-only (surfaces, never moves/deletes), and fail-soft — a monitor
error logs and returns, it never breaks day-close. It reuses the git index rather
than re-walking the tree.

D-canonical-memory-consolidation-2026-06-23 / T-path-moves-monitor.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger("devlab.path_moves_monitor")

_REPO = Path(__file__).resolve().parents[2]
_REGISTRY = _REPO / "devlab" / "runtime" / "memory" / "rules" / "path_moves.json"


def load_registry(path: "Optional[Path]" = None) -> dict:
    """Read the path-moves registry JSON."""
    return json.loads(Path(path or _REGISTRY).read_text(encoding="utf-8"))


def git_tracked_files(repo: "Optional[Path]" = None) -> list:
    """The file index = `git ls-files` (no re-walk of the tree)."""
    out = subprocess.run(
        ["git", "-C", str(repo or _REPO), "ls-files"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line for line in out.splitlines() if line]


def _is_artifact(path: str, registry: dict) -> bool:
    """A path is a dev-process artifact if it carries a store suffix or lives in a
    store sub-directory (decisions/, tickets/, slates/, ...)."""
    if any(path.endswith(sfx) for sfx in registry.get("artifact_suffixes", [])):
        return True
    segs = path.split("/")
    return any(d in segs for d in registry.get("artifact_dir_globs", []))


def _suggest(path: str, registry: dict) -> str:
    for mv in registry.get("moves", []):
        if path.startswith(mv["from"]):
            return path.replace(mv["from"], mv["to"], 1)
    return registry.get("canonical_home", "")


def scan(files: list, registry: dict) -> list:
    """STUB — replaced by the real detector in the next commit."""
    return []


def run(*, repo: "Optional[Path]" = None, emit: bool = True) -> list:
    """Load the registry, scan the file index, raise one deduped alarm per finding.

    Fail-soft: any error logs and returns ``[]`` — never raises into day-close.
    """
    try:
        registry = load_registry()
        findings = scan(git_tracked_files(repo), registry)
    except Exception as exc:  # noqa: BLE001 — monitor must never break day-close
        log.error("path-moves monitor failed (fail-soft): %s", exc)
        return []
    if emit and findings:
        try:
            from unseen_university import system_alarms
            for f in findings:
                system_alarms.raise_alarm(
                    signature=f"noncanonical-artifact:{f['path']}",
                    caller="path_moves_monitor",
                    message=(f"dev-process artifact at a non-canonical path: {f['path']} "
                             f"({f['reason']}) — canonical: {f['suggested']}"),
                    fatal=False,
                )
        except Exception as exc:  # noqa: BLE001 — alarm failure is non-fatal
            log.error("path-moves monitor: alarm emit failed (fail-soft): %s", exc)
    return findings


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    findings = run()
    if findings:
        print(f"path-moves monitor: {len(findings)} non-canonical artifact(s) — alarms raised:")
        for f in findings:
            print(f"  [{f['reason']}] {f['path']} -> {f['suggested']}")
    else:
        print("path-moves monitor: clean — every dev-process artifact is in the canonical home.")
    return 0  # read-only surface; never a non-zero gate


if __name__ == "__main__":
    raise SystemExit(main())
