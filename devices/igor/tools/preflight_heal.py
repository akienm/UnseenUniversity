"""
preflight_heal.py — Pre-flight pytest failure classifier + self-healer.

When pe_chain's pre-flight test run fails on a known pattern (e.g. a test
making a live HTTP call without a mock), this module recognizes the pattern
and applies a narrow remedy (e.g. add a pytest.mark.skipif decorator gated
on IGOR_LIVE_TESTS). The repaired test file is committed as a precursor
before pe_chain proceeds with the main ticket's work — turning "already
broken" from a dead-end into a cleanup opportunity.

Add a new recognizer by subclassing Recognizer and appending to RECOGNIZERS.
Each recognizer answers:
  - matches(failure_text: str) -> bool
  - remedy(failure_text: str, repo_root: Path) -> list[EditDict]

Recognizers MUST be narrow. A wrong match rewrites test files and commits
them — false positives corrupt the suite.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class EditDict:
    """A single file edit: replace old_string with new_string in file."""

    file: str  # repo-root-relative
    old_string: str
    new_string: str


@dataclass
class HealResult:
    healed: bool = False
    recognizer: str | None = None
    edits: list[EditDict] = field(default_factory=list)
    commit_sha: str | None = None
    unfixable: list[str] = field(default_factory=list)


class Recognizer(ABC):
    """
    Pairs a pytest-failure pattern with a narrow remedy.

    Subclasses set `name` and implement matches() + remedy(). Keep the
    pattern narrow — a false match corrupts tests. Each recognizer should
    target exactly one rot shape (one error signature → one repair).
    """

    name: str = "base"

    @abstractmethod
    def matches(self, failure_text: str) -> bool:
        """Return True if this recognizer's pattern appears in pytest output."""

    @abstractmethod
    def remedy(self, failure_text: str, repo_root: Path) -> list[EditDict]:
        """Return list of edits to apply, or [] if no remedy possible."""


# Matches pytest FAILED / ERROR lines like:
#   FAILED tests/test_foo.py::test_bar - AssertionError
#   ERROR tests/test_baz.py::TestClass::test_quux
_FAILED_LINE_RE = re.compile(
    r"^(?:FAILED|ERROR) (tests/[a-zA-Z0-9_/.-]+\.py)::([a-zA-Z0-9_:.]+)",
    re.MULTILINE,
)


class SocketRecvNoMockRecognizer(Recognizer):
    """
    Match: pytest failure containing urllib3 socket.recv timeout — signals
    a test that hits live network without a mock.

    Remedy: add @pytest.mark.skipif(not os.getenv("IGOR_LIVE_TESTS"), ...)
    above the failing test function. Test stays valuable in a live-tests
    CI lane; skipped by default so pre-flight doesn't block on it.
    """

    name = "socket-recv-no-mock"

    _SIGNALS = ("socket.timeout", "self._sock.recv", "ReadTimeout")

    def matches(self, failure_text: str) -> bool:
        return any(sig in failure_text for sig in self._SIGNALS)

    def remedy(self, failure_text: str, repo_root: Path) -> list[EditDict]:
        m = _FAILED_LINE_RE.search(failure_text)
        if not m:
            return []
        rel_path = m.group(1)
        # handle Class::method or bare method
        func_name = m.group(2).split("::")[-1]

        file_path = repo_root / rel_path
        if not file_path.exists():
            return []

        content = file_path.read_text()
        func_def_re = re.compile(
            rf"^([ \t]*)def {re.escape(func_name)}\(", re.MULTILINE
        )
        fm = func_def_re.search(content)
        if not fm:
            return []

        indent = fm.group(1)
        old = fm.group(0)
        # Idempotency: if the decorator is already there, no remedy needed
        start = max(0, fm.start() - 200)
        preamble = content[start : fm.start()]
        if "IGOR_LIVE_TESTS" in preamble:
            return []

        decorator = (
            f"{indent}@pytest.mark.skipif(\n"
            f'{indent}    not os.getenv("IGOR_LIVE_TESTS"),\n'
            f'{indent}    reason="requires live network '
            f'— gated on IGOR_LIVE_TESTS",\n'
            f"{indent})\n"
        )
        new = decorator + old
        return [EditDict(file=rel_path, old_string=old, new_string=new)]


RECOGNIZERS: list[Recognizer] = [SocketRecvNoMockRecognizer()]


def classify(pytest_output: str, repo_root: Path) -> HealResult:
    """
    Match pytest failure output against registered recognizers.

    First recognizer that matches AND produces a non-empty remedy wins —
    one failure gets one remedy. If nothing matches, return unfixable.
    No side effects.
    """
    for rec in RECOGNIZERS:
        if not rec.matches(pytest_output):
            continue
        edits = rec.remedy(pytest_output, repo_root)
        if not edits:
            log.info("preflight_heal: %s matched but produced no remedy", rec.name)
            continue
        log.info("preflight_heal: %s matched with %d edit(s)", rec.name, len(edits))
        return HealResult(healed=True, recognizer=rec.name, edits=edits)
    return HealResult(healed=False, unfixable=[pytest_output[:400]])


def _ensure_imports(content: str, inserted_text: str) -> str:
    """Add `import os` / `import pytest` if inserted_text uses them."""
    needed: list[str] = []
    if "pytest.mark" in inserted_text and not re.search(
        r"^import pytest\b|^from pytest\b", content, re.MULTILINE
    ):
        needed.append("import pytest")
    if "os.getenv" in inserted_text and not re.search(
        r"^import os\b", content, re.MULTILINE
    ):
        needed.append("import os")
    if not needed:
        return content

    lines = content.splitlines(keepends=True)
    insert_idx = 0
    # Insert after the last existing top-level import (or after module docstring)
    for i, line in enumerate(lines):
        if line.startswith(("import ", "from ")):
            insert_idx = i + 1
    for imp in needed:
        lines.insert(insert_idx, imp + "\n")
        insert_idx += 1
    return "".join(lines)


def apply_heal(result: HealResult, repo_root: Path) -> bool:
    """Apply all edits in result and ensure required imports. In-place."""
    for edit in result.edits:
        fp = repo_root / edit.file
        content = fp.read_text()
        if edit.old_string not in content:
            log.info("HEAL: %s old_string not in %s — aborting", edit.file, edit.file)
            return False
        new_content = content.replace(edit.old_string, edit.new_string, 1)
        new_content = _ensure_imports(new_content, edit.new_string)
        fp.write_text(new_content)
    return True


def commit_heal(result: HealResult, repo_root: Path) -> str | None:
    """Git-add + commit the heal edits. Return commit sha or None on failure."""
    files = [str(repo_root / e.file) for e in result.edits]
    try:
        subprocess.run(
            ["git", "add", *files],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            timeout=15,
        )
        msg = f"chore: preflight auto-heal ({result.recognizer})"
        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            timeout=30,
        )
        sha_result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        sha = sha_result.stdout.strip()
        log.info(
            "HEAL: committed %d edit(s) via %s (%s)",
            len(result.edits),
            result.recognizer,
            sha,
        )
        return sha
    except Exception as exc:
        log.warning("HEAL: commit failed — %s", exc)
        return None


def heal_and_commit(pytest_output: str, repo_root: Path) -> HealResult:
    """Full flow: classify, apply edits, git-commit. Returns updated HealResult."""
    result = classify(pytest_output, repo_root)
    if not result.healed:
        return result
    if not apply_heal(result, repo_root):
        result.healed = False
        result.unfixable = [f"{result.recognizer}: apply_heal returned False"]
        return result
    sha = commit_heal(result, repo_root)
    if not sha:
        result.healed = False
        result.unfixable = [f"{result.recognizer}: commit_heal returned None"]
        return result
    result.commit_sha = sha
    return result
