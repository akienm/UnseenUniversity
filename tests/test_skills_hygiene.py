"""Regression guard for skill correctness (D-skills-two-products).

The skills are our daily tooling — a stale path or dead credential in a skill
breaks US (it broke day-close on 2026-06-25). This guard scans the canonical
repo skills/ tree for the known drift/breakage classes and asserts zero, so a
regression can't silently reintroduce them.

Scope: repo skills/ ONLY (the canonical source; ~/.claude/skills is retired by
the single-source flip). Scans SKILL.md, extensionless `run` scripts (the
class a *.md/*.py guard would miss), and top-level *.md like workflow.md.
"""
from __future__ import annotations

import re
from pathlib import Path

_SKILLS = Path(__file__).resolve().parents[1] / "skills"


def _skill_files():
    """All skill text/script files (SKILL.md, run scripts, top-level md)."""
    files = list(_SKILLS.rglob("SKILL.md"))
    files += [p for p in _SKILLS.rglob("run") if p.is_file()]
    files += list(_SKILLS.glob("*.md"))
    # questions/ is a data corpus, not a skill; manifest is structured config.
    return [f for f in files if "questions/" not in str(f.relative_to(_SKILLS))]


# Lines where the literal is a credential-SCANNER needle (the day-close-audit
# Step-15 grep that hunts for hardcoded creds MUST name what it scans for).
_SCANNER_NEEDLE = re.compile(r'-e "(choose_a_password|Igor-wild-0001)"')


def _violations(pattern: str, *, exclude_dirs: tuple[str, ...] = ()) -> list[str]:
    rx = re.compile(pattern)
    out = []
    for f in _skill_files():
        rel = str(f.relative_to(_SKILLS))
        if any(rel.startswith(d) for d in exclude_dirs):
            continue
        for i, line in enumerate(f.read_text(errors="ignore").splitlines(), 1):
            if _SCANNER_NEEDLE.search(line):
                continue
            if rx.search(line):
                out.append(f"{rel}:{i}: {line.strip()[:100]}")
    return out


def test_no_dead_db_credentials():
    v = _violations(r"choose_a_password")
    assert not v, "dead DB password literal in skills:\n" + "\n".join(v)


def test_no_dead_instance_db_name():
    v = _violations(r"Igor-wild-0001")
    assert not v, "dead instance/DB name in skills (use $UU_HOME_DB_URL):\n" + "\n".join(v)


def test_no_lab_claudecode_path():
    # devlab/claudecode is correct; bare lab/claudecode is the stale path.
    v = _violations(r"(?<!dev)lab/claudecode")
    assert not v, "stale lab/claudecode path (use devlab/claudecode):\n" + "\n".join(v)


def test_no_decided_command():
    v = _violations(r"/decided\b")
    assert not v, "stale /decided command (use /sorted):\n" + "\n".join(v)


def test_no_theigors_path_refs():
    # audit-workspace legitimately scans ~/TheIgors* archive dirs — that's its job.
    v = _violations(r"THEIGORS_HOME|\$HOME/TheIgors|~/TheIgors", exclude_dirs=("audit-workspace",))
    assert not v, "stale TheIgors path refs (use UU root):\n" + "\n".join(v)


def test_no_autocompact_calls():
    # The autocompact skill itself stays; nothing else may invoke it or the dance.
    v = _violations(r"/autocompact\b|uucompactclaude", exclude_dirs=("autocompact",))
    assert not v, "/autocompact calls remain (native compact supersedes):\n" + "\n".join(v)


def test_no_missing_script_refs():
    # Scripts referenced by skills that have never existed anywhere in the repo.
    v = _violations(r"validate_files\.py|github_sync\.py|docs_sync\.py")
    assert not v, "refs to never-built scripts (remove the step):\n" + "\n".join(v)
