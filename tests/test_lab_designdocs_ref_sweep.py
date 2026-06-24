"""Proof for T-lab-designdocs-ref-sweep (D-canonical-memory-consolidation).

The live fileset (code, skills, docs, tests, scripts, bin) no longer references the
retired `lab/claudecode` or `lab/design_docs` paths — they point at the canonical
`devlab/claudecode` / `devlab/runtime/memory/` instead. Every remaining genuine
`lab/` mention is on the explicit allowlist below, each with a documented reason.

Scope notes:
- `devlab/...` is NOT a violation (negative-lookbehind `(?<!dev)` excludes it).
- The historical store (tickets/decisions/sessions/slates/proofs), the doomed
  `devlab/design_docs/` dir, `CLAUDE.md`, the path-moves registry, the two
  fixture tests, and the legacy-backfill skill legitimately retain the strings
  and are excluded from the scan — they are not live read-refs.

RED before the sweep (skills/code/docs referenced the dead paths), GREEN after.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

# Files that legitimately keep a genuine (non-devlab) lab/ reference:
# (cc_queue.py + cc_nightly_palace_updates.py were here until T-cc-queue-rollup-misfiles-lab
#  and T-cc-nightly-reads-decision-md fixed their lab/ refs — dropped from the allowlist.)
ALLOWLIST = {
    # Historical records / legacy tooling — the mention is accurate-as-history:
    "devlab/claudecode/pending/handoff-2026-05-20-windows-session.md",  # historical handoff note
    "devlab/runtime/memory/SPEC.md",                    # migration-provenance prose (what the store ingested FROM)
    "docs/palace_schema.md",                            # legacy Postgres-palace seeding, superseded by the fs store
    "scripts/palace_seed_decisions.py",                 # legacy Postgres-palace seeding, superseded by the fs store
}


def _genuine_lab_ref_files() -> set:
    """Tracked files that reference a genuine (non-devlab) retired lab/ path,
    excluding the historical store, doomed dirs, fixtures, and intentional docs."""
    out = subprocess.run(
        ["git", "-C", str(_REPO), "grep", "-lP", r"(?<!dev)lab[./](claudecode|design_docs)",
         "--",
         ":!devlab/runtime/memory/tickets/", ":!devlab/runtime/memory/decisions/",
         ":!devlab/runtime/memory/sessions/", ":!devlab/runtime/memory/slates/",
         ":!devlab/runtime/memory/proofs/",
         ":!devlab/design_docs/", ":!CLAUDE.md",
         ":!devlab/runtime/memory/rules/path_moves.json",
         ":!tests/test_skill_write_paths_canonical.py", ":!tests/test_path_moves_monitor.py",
         ":!tests/test_lab_designdocs_ref_sweep.py",  # this proof's own fixture literals
         ":!tests/test_cc_queue_rollup_no_lab_write.py",   # proof fixture describes the retired path
         ":!tests/test_cc_nightly_reads_json_decisions.py",  # proof fixture describes the retired path
         ":!skills/migrate-decisions/"],
        capture_output=True, text=True,
    ).stdout
    return {line for line in out.splitlines() if line}


def test_no_live_file_references_retired_lab_paths():
    """Proof node (one intention): no live file references a retired lab/ path
    outside the documented allowlist."""
    offenders = _genuine_lab_ref_files()
    stray = offenders - ALLOWLIST
    assert stray == set(), f"live files still reference retired lab/ paths (not allowlisted): {sorted(stray)}"
