#!/usr/bin/env python3
"""rules_citation_check — every rule the skills cite must exist as a readable artifact.

The audit skills cite rules as ``unseenuniversity/rules/<name>[/<sub>]``. Before
T-rules-store-materialize those citations were phantom: 14 distinct names, one file
on disk — the gates enforced law a fresh builder could not read (rebuildability-diff
F1, CRITICAL). This check makes that regression detectable: it scans skills/ for
rule citations and resolves each against devlab/runtime/memory/rules/, where an
artifact matches when its filename namespace segment equals the cited name
(memory_emit convention: ``<emitter>.<name>.<yyyymmdd>.<hhmmssuuuuuu>.json``).

A cited sub-path (e.g. ``capability-protocol/two-sided-build``) additionally
requires the sub-name to appear inside the artifact JSON. Template placeholders
(``rules/<name>``) are skipped. Exit 0 iff every citation resolves.

Run: python3 devlab/claudecode/rules_citation_check.py
"""
from __future__ import annotations

import glob
import os
import re
import sys

# devlab/claudecode/rules_citation_check.py -> repo root is three dirs up.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_CITE_RE = re.compile(r"unseenuniversity/rules/([A-Za-z0-9_<>-]+(?:/[A-Za-z0-9_<>-]+)*)")


def _rules_root() -> str:
    return os.environ.get(
        "UU_MEMORY_ROOT", os.path.join(_REPO, "devlab", "runtime", "memory")
    ) + os.sep + "rules"


def collect_citations(skills_dir: str) -> dict[str, set[str]]:
    """Map cited rule path -> set of citing skill files. Placeholders excluded."""
    cites: dict[str, set[str]] = {}
    for path in glob.glob(os.path.join(skills_dir, "**", "*.md"), recursive=True):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for m in _CITE_RE.finditer(text):
            cited = m.group(1).rstrip("/")
            if "<" in cited or ">" in cited:
                continue  # template placeholder, not a concrete citation
            cites.setdefault(cited, set()).add(os.path.relpath(path, _REPO))
    return cites


def resolve(cited: str, rules_root: str) -> tuple[bool, str]:
    """Resolve one citation. Returns (ok, detail)."""
    segs = cited.split("/")
    name, subs = segs[0], segs[1:]
    hits = [
        p for p in glob.glob(os.path.join(rules_root, "*.json"))
        if f".{name}." in os.path.basename(p) or os.path.basename(p) == f"{name}.json"
    ]
    if not hits:
        return False, "no artifact"
    concrete_subs = [s for s in subs if "<" not in s]
    for sub in concrete_subs:
        if not any(sub in open(p, encoding="utf-8").read() for p in hits):
            return False, f"artifact exists but sub-entry {sub!r} not found in it"
    return True, os.path.relpath(hits[0], _REPO)


def main() -> int:
    skills_dir = os.path.join(_REPO, "skills")
    rules_root = _rules_root()
    cites = collect_citations(skills_dir)
    unresolved = []
    for cited in sorted(cites):
        ok, detail = resolve(cited, rules_root)
        status = "ok " if ok else "MISSING"
        print(f"{status} unseenuniversity/rules/{cited} -> {detail}")
        if not ok:
            unresolved.append((cited, sorted(cites[cited])))
    print(f"\ncitations={len(cites)} unresolved={len(unresolved)}")
    for cited, files in unresolved:
        print(f"  MISSING {cited}  cited by: {', '.join(files)}")
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
