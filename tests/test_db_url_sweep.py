"""Proof for T-uu-sweep-db-connection-string + T-uu-dburl-name-unify.

No live CODE embeds the hardcoded DB-password fallback, and no live code reads the
legacy IGOR_HOME_DB_URL env var — every DB-URL resolution goes through
identity.home_db_url(), which reads UU_HOME_DB_URL (legacy alias accepted) and RAISES
if unset (never a baked credential).

RED before: ~75 files embedded os.environ.get("UU_HOME_DB_URL", "<weak-password url>").
GREEN after the sweep (89 modules, all DB-URL resolution lazy / call-time).

Documented exclusions — NOT live-code fallbacks:
- tests/ + *.example / *.template — throwaway / placeholder credentials.
- the two credential DETECTORS (audit_check_hardcoded_instance.py, audit_checks.json) —
  they search FOR the literal; the needle is fragmented / regex'd so the source holds none.
- unseen_university/identity.py — the resolver; intentionally accepts IGOR_HOME_DB_URL
  as a legacy alias.
- devlab/runtime/memory/ — append-only historical records (decisions/tickets/slates); never rewritten.
- skills/*.md — skill-doc psql examples (separate skills-rework, T-skills-palace-db-to-fs-store).
- config/profiles/*.yaml — subsystem URLs embedding the password need a config-layer change
  (the loader must compose them from env); tracked at T-uu-config-profile-db-creds.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]


def _grep_files(pattern: str, *pathspec: str) -> list[str]:
    return subprocess.run(
        ["git", "-C", str(_REPO), "grep", "-lE", pattern, "--", *pathspec],
        capture_output=True, text=True,
    ).stdout.split()


def test_no_db_password_fallback_in_live_code():
    """Proof node (connection-string): no live .py/script embeds the password fallback."""
    needle = "choose_a" + "_password"  # fragmented so this test isn't its own false positive
    hits = _grep_files(needle, "*.py", "uu",
                       ":!tests/", ":!*audit_check_hardcoded_instance.py")
    assert hits == [], f"live code still embeds the DB-password fallback: {hits}"


def test_no_legacy_igor_home_db_url_reads_in_live_code():
    """Proof node (name-unify): no live .py/script reads the legacy IGOR_HOME_DB_URL."""
    hits = _grep_files("IGOR_HOME_DB_URL", "*.py", "uu",
                       ":!tests/", ":!unseen_university/identity.py")
    assert hits == [], f"live code still reads legacy IGOR_HOME_DB_URL: {hits}"
