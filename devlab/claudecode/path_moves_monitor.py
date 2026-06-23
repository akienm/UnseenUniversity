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
import re
import subprocess
from pathlib import Path
from typing import Optional

log = logging.getLogger("devlab.path_moves_monitor")

# Emission filename signature: <emitter>.<ns>.yyyymmdd.hhmmss[uuuuuu].json
_STAMP_RE = re.compile(r"\.\d{8}\.\d{6,}\.json$")

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
    """A path is a dev-process artifact if it carries a store suffix (.dsb,
    .slate.txt), matches the emission filename signature (`.<stamp>.json`), or is
    a legacy `D-*.md` decision stub. Intrinsic to the file — no dir-name guessing,
    so unrelated `rules/`, `notes/`, `projects/` dirs don't false-positive."""
    base = path.rsplit("/", 1)[-1]
    if any(path.endswith(sfx) for sfx in registry.get("artifact_suffixes", [])):
        return True
    if _STAMP_RE.search(base):
        return True
    return base.startswith("D-") and base.endswith(".md")


def _suggest(path: str, registry: dict) -> str:
    for mv in registry.get("moves", []):
        if path.startswith(mv["from"]):
            return path.replace(mv["from"], mv["to"], 1)
    return registry.get("canonical_home", "")


def scan(files: list, registry: dict) -> list:
    """Flag every file under a retired path, and every dev-process artifact outside
    the canonical home. Pure (no I/O) so it is deterministic to test."""
    home = registry.get("canonical_home", "").rstrip("/") + "/"
    retired = [p.rstrip("/") + "/" for p in registry.get("retired_paths", [])]
    findings = []
    for f in files:
        hit = next((r for r in retired if f.startswith(r)), None)
        if hit:
            findings.append({"path": f, "reason": "under-retired-path",
                             "suggested": _suggest(f, registry)})
        elif _is_artifact(f, registry) and not f.startswith(home):
            findings.append({"path": f, "reason": "artifact-outside-canonical-home",
                             "suggested": home})
    return findings


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
            # Group retired-path findings by root → ONE alarm per retired root (not
            # per file; a populated retired dir can hold a hundred artifacts). Stray
            # artifacts outside the home are rarer and surprising → one alarm each.
            roots: dict = {}
            strays = []
            for f in findings:
                if f["reason"] == "under-retired-path":
                    root = next((r for r in registry.get("retired_paths", [])
                                 if f["path"].startswith(r)), f["path"])
                    roots.setdefault(root, []).append(f["path"])
                else:
                    strays.append(f)
            for root, paths in roots.items():
                system_alarms.raise_alarm(
                    signature=f"retired-path-populated:{root}",
                    caller="path_moves_monitor",
                    message=(f"{len(paths)} dev-process artifact(s) still under retired path "
                             f"{root!r} — move to {registry.get('canonical_home')} or remove "
                             f"(e.g. {paths[0]})"),
                    fatal=False,
                )
            for f in strays:
                system_alarms.raise_alarm(
                    signature=f"noncanonical-artifact:{f['path']}",
                    caller="path_moves_monitor",
                    message=(f"dev-process artifact outside the canonical home: {f['path']} "
                             f"— canonical: {f['suggested']}"),
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
