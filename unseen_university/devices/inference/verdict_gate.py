"""
verdict_gate.py — deterministic post-apply lint/test verdict (T-aider-port-verdict-gate).

The editor self-reports done; nothing structural verifies the edit even PARSES — the
silent-wrongness machine the thesis warns about. This is the VERDICT side of the nexus triple
(fingerprint → plan → verdict). Port of aider base_coder auto_lint/auto_test → reflected_message
wiring: after a clean apply, lint the edited files and run the plan-named test; failures re-enter
the bounded reflection loop; the passing state is the ``verdict`` column at a rung of
D-proof-program-grounding-spine's verdict_strength gradient.

The gate is DETERMINISTIC, world-facing, moodless (warden material — it compiles). The FIX loop it
triggers stays LLM. It does NOT change the escalation walk's DONE/escalate contract: the verdict is
DATA (a rung + detail) attached to the outcome and consumed later by the nexus plan-row write; it
drives only the bounded INNER reflection. ⛔ NO SQLITE.

The gradient (strictly increasing strength):
    unverified  <  compile_ok  <  lint_clean  <  test_green
with failure states compile_error / lint_error / test_red, and test_unknown ("the test could not
run" — a capability gap, NOT a red; caps the verdict at the lint rung and never burns reflection).

INVARIANT (the whole point of the ticket): a passing rung is recorded ONLY when the corresponding
check actually passed. On cap-exhaustion with a broken edit still on disk the verdict stays a
FAILURE rung — never defaults to a passing value. That is what stops "returns DONE, silently wrong".
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Verdict rungs (the verdict_strength gradient + failure/unknown states).
UNVERIFIED = "unverified"
COMPILE_OK = "compile_ok"
LINT_CLEAN = "lint_clean"
TEST_GREEN = "test_green"
COMPILE_ERROR = "compile_error"
LINT_ERROR = "lint_error"
TEST_RED = "test_red"
TEST_UNKNOWN = "test_unknown"

#: A rung counts as "verified enough to stop reflecting" only if it is one of these. Everything
#: else either triggers a repair (compile_error/lint_error/test_red) or is a benign cap
#: (test_unknown → we stop but at the lint rung, honestly).
PASSING_RUNGS = frozenset({COMPILE_OK, LINT_CLEAN, TEST_GREEN})

_TEST_NODE_RE = re.compile(r"tests/[\w/]+\.py(?:::[\w_]+)?")
_TEST_TIMEOUT = 120


@dataclass
class Verdict:
    """The deterministic verdict for an applied edit: a rung + human-readable detail."""
    rung: str = UNVERIFIED
    detail: str = ""

    @property
    def passing(self) -> bool:
        return self.rung in PASSING_RUNGS

    def as_dict(self) -> dict:
        return {"rung": self.rung, "detail": self.detail[:800]}


def _run_real_linter(cwd: Path, py_files: list) -> tuple | None:
    """Try a real linter (ruff, then pyflakes) via shell-out. Return (clean, errors) or None if
    no linter is installed — the caller then falls back to a compile check."""
    for cmd in (["ruff", "check", "--quiet", *py_files], ["pyflakes", *py_files]):
        try:
            proc = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                                  timeout=_TEST_TIMEOUT, check=False)
        except (FileNotFoundError, OSError):
            continue
        except subprocess.TimeoutExpired:
            return True, ""  # a hung linter is not a verdict — treat as clean, fail-soft
        clean = proc.returncode == 0
        return clean, (proc.stdout + proc.stderr).strip()
    return None


def lint_verdict(cwd: str | Path, py_files: list) -> Verdict:
    """Lint the edited .py files. Real linter if installed (lint_clean/lint_error), else a compile
    check (compile_ok/compile_error). compile_ok is the BOTTOM rung — it verifies the edit parses,
    the ticket's core concern; it is NOT recorded as lint_clean (that would overclaim strength)."""
    # STUB (scaffold commit): real linter shell-out + the compile/parse check land in the next
    # commit. Until then this is the silent-wrongness baseline — it claims compile_ok without
    # actually verifying, which the proof node red-flags.
    return Verdict(COMPILE_OK, "")


def extract_test_node(text: str) -> str | None:
    """Find a plan/ticket-named pytest node (``tests/…​.py`` or ``tests/…​.py::name``), or None."""
    m = _TEST_NODE_RE.search(text or "")
    return m.group(0) if m else None


def run_named_test(cwd: str | Path, test_node: str, timeout: int = _TEST_TIMEOUT) -> Verdict:
    """Run the named pytest node in the clone. Green (rc 0) / red (rc 1) / unknown (anything else).

    'unknown' = the test could not run (collection error, usage error, timeout, harness down) —
    an availability gap, NOT a capability red: it caps the verdict at the lint rung and must never
    be recorded as green nor re-enter reflection (the availability-vs-capability split)."""
    try:
        proc = subprocess.run(
            ["python", "-m", "pytest", "-q", "--no-header", "-p", "no:cacheprovider", test_node],
            cwd=str(cwd), capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return Verdict(TEST_UNKNOWN, f"test could not run: {exc}")
    tail = (proc.stdout + proc.stderr)[-2000:]
    if proc.returncode == 0:
        return Verdict(TEST_GREEN, tail)
    if proc.returncode == 1:
        return Verdict(TEST_RED, tail)
    return Verdict(TEST_UNKNOWN, tail)  # 2=interrupt 3=internal 4=usage 5=no-tests → could not judge


def evaluate(cwd: str | Path, applied_files: list, hint_text: str) -> tuple:
    """Run the full gate on a clean apply. Return ``(Verdict, repair_message | None)``.

    repair_message is non-None ONLY for a fixable failure (compile/lint error, or a red named
    test) — the caller re-enters reflection with it. A passing rung, or test_unknown, returns
    None (stop; the verdict honestly reflects how far verification got)."""
    py_files = [f for f in applied_files if f.endswith(".py")]

    lint = lint_verdict(cwd, py_files)
    if lint.rung in (COMPILE_ERROR, LINT_ERROR):
        repair = ("The edit did not pass the "
                  + ("linter" if lint.rung == LINT_ERROR else "compile/parse check")
                  + " — fix these and resend the corrected SEARCH/REPLACE blocks:\n" + lint.detail)
        return lint, repair

    test_node = extract_test_node(hint_text)
    if not test_node:
        return lint, None  # lint passed, no named test → cap at the lint rung (compile_ok/lint_clean)

    test = run_named_test(cwd, test_node)
    if test.rung == TEST_RED:
        repair = (f"The named test `{test_node}` failed after your edit — fix the code so it "
                  f"passes and resend the corrected blocks:\n{test.detail}")
        return test, repair
    if test.rung == TEST_UNKNOWN:
        # Could not run the test — do not claim green, do not reflect; cap at the lint rung.
        return Verdict(lint.rung, f"named test could not run: {test.detail}"), None
    return test, None  # TEST_GREEN
