"""Proof for T-skills-palace-db-to-fs-store (context-load slice).

context-load reads the canonical filesystem store, not the retired Postgres
palace — and it must do so from ANY cwd. The bug: the run script defaulted
UU_ROOT to "." (cwd), so the decision/memory steps (2a/2b/3) silently no-op'd
whenever cwd != repo root (e.g. post-compact, the first thing run). The fix
resolves UU_ROOT via uu_root() (env-var first, then the package __file__ chain).

RED before: run from a tmp cwd with UU_ROOT unset surfaced no decisions, and the
SKILL.md doc still described psql/memory_palace steps. GREEN after.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_RUN = _REPO / "skills" / "context-load" / "run"
_SKILL_MD = _REPO / "skills" / "context-load" / "SKILL.md"


def test_context_load_surfaces_decisions_from_any_cwd(tmp_path):
    """Proof node (one intention): the briefing resolves the filesystem store
    and surfaces recent decisions WITH STATUS even when run from a non-repo cwd
    with UU_ROOT unset — the case that silently dropped Steps 2a/2b/3."""
    env = {k: v for k, v in __import__("os").environ.items() if k != "UU_ROOT"}
    out = subprocess.run(
        [sys.executable, str(_RUN)],
        cwd=str(tmp_path),          # NOT the repo root
        env=env,                    # UU_ROOT explicitly unset
        capture_output=True, text=True, timeout=30,
    ).stdout

    assert "Recent decisions:" in out, f"Step 2a dropped (no decisions surfaced):\n{out}"
    assert "• D-" in out, f"no D-id decision line surfaced:\n{out}"
    assert "Active decisions:" in out, f"Step 3 dropped:\n{out}"
    # Step 3 must show real status from the JSON body, e.g. "[open]"
    assert "[open]" in out or "[superseded" in out, f"Step 3 shows no status bracket:\n{out}"


def test_executor_and_doc_make_no_palace_db_calls():
    """Guard: neither the executor nor the doc INVOKES the retired Postgres
    palace. Checks DB-usage patterns that appear only in real queries (a bare
    'psql'/'memory_palace' grep can't distinguish a query from prose that
    documents the palace's absence, or a legacy function name)."""
    for label, path in (("run", _RUN), ("SKILL.md", _SKILL_MD)):
        text = path.read_text(encoding="utf-8")
        for pattern in ("choose_a_password", "psql postgresql", "FROM memory_palace",
                        "FROM adc.palace", "decisions_log.dsb", "IGOR_HOME_DB_URL"):
            assert pattern not in text, f"{label} still invokes the palace DB: {pattern!r}"
