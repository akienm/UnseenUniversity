"""Grep-gate: no live code references retired runtime paths.

This is the cleanup's Phase-1 artifact AS CODE and its regression guard: the old
igor/uc/adc runtime locations must not survive in live code, or the retired dirs
regenerate on the next boot (the write-path-split bug CLAUDE.md warns about).

Scope: LIVE CODE ONLY. devlab/runtime/memory/ is excluded — those are historical
records (tickets/decisions/slates) that legitimately *mention* old paths; rewriting
them would falsify history. .git is excluded.

Each token is added to FORBIDDEN as its migration lands (canonical target in the
comment). A token here means: zero occurrences in live code, full stop.
"""
import re
from pathlib import Path

import pytest

from unseen_university._uu_root import uu_root

ROOT = Path(uu_root())

# Live-code roots scanned by the gate.
LIVE_ROOTS = ["unseen_university", "devlab/claudecode", "skills"]
SCAN_SUFFIXES = {".py", ".sh", ".yaml", ".yml", ".toml", ".cfg", ".md"}
EXCLUDE_PARTS = {".git", "runtime", "node_modules", "__pycache__", ".venv"}

# token -> canonical replacement (the migration that retired it).
FORBIDDEN = {
    "datacenter_logs": "uu_home()/logs/<device>/<stream>/",
    "Igor-wild-0001": "the live instance via identity.instance_id() (archived; never name it in code)",
    # (added as each migration lands: ".TheIgors", "IGOR_HOME",
    #  "lab/design_docs", "ADC_LOG_ROOT" ...)
}


def _live_files():
    for root in LIVE_ROOTS:
        base = ROOT / root
        if not base.exists():
            continue
        for f in base.rglob("*"):
            if f.suffix not in SCAN_SUFFIXES:
                continue
            if EXCLUDE_PARTS & set(f.parts):
                continue
            # never flag the gate's own definition of the tokens
            if f.name == "test_no_retired_runtime_paths.py":
                continue
            yield f


@pytest.mark.parametrize("token", sorted(FORBIDDEN))
def test_no_retired_path_token_in_live_code(token):
    pat = re.compile(re.escape(token))
    hits = []
    for f in _live_files():
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if pat.search(line):
                hits.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()[:100]}")
    assert not hits, (
        f"retired path token {token!r} survives in live code "
        f"(canonical: {FORBIDDEN[token]}):\n" + "\n".join(hits)
    )
