"""AiderRunner — standalone, bus-free headless aider invocation for one coding task.

ZERO rack dependency: no bus, no shim, no ~/.granny, no Postgres. This module
generalizes devlab/claudecode/aider_smoke.py from a fixed sandbox to
(repo_source, task_message) -> validated AiderResult, and is both importable and
CLI-invocable:

    python -m unseen_university.devices.aider.runner \
        --repo /path/to/repo --message "implement X" --test tests/test_x.py

so a fresh box (including a Windows exploration box) with the repo + an aider
venv + Hex reachable can drive an aider build BEFORE any rack (Granny/bus) exists.
AiderDevice (device.py) layers dispatch on top of this — it does not reimplement it.

What build() does, in order:
  1. Clone the target repo to a THROWAWAY workdir (source is never touched).
  2. Cut a work BRANCH (never main — 124553ee / branch-not-main lesson).
  3. Run aider headless via subprocess (aider stays external — never imported).
  4. Stage edits and compute the changed-file set.
  5. Apply the OBJECTIVE gate: tests-green (load-bearing correctness) + diff-scope
     (a safety predicate: block if aider edited tests or escaped the repo).
  6. Commit the edits to the branch (evidence survives even on a failed gate).
  7. Return an AiderResult for the caller (device -> CC validation).

Windows-portability: all paths via pathlib, endpoint/bin via env (see consts),
no shell=True, no bash-isms. git and an aider venv must be present on the box.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .consts import DEFAULT_MODEL, HEX_OLLAMA, aider_bin

# A changed path matching any of these is a TEST file — aider must never edit
# tests to make them pass (the "did it cheat" guard). Repo-relative, POSIX slashes.
_TEST_PATTERNS = (
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)test_[^/]*\.py$"),
    re.compile(r"(^|/)[^/]*_test\.py$"),
    re.compile(r"(^|/)conftest\.py$"),
)
# Protected infra aider must not touch on a normal coding ticket.
_PROTECTED_PATTERNS = (
    re.compile(r"(^|/)\.github/"),
    re.compile(r"(^|/)\.git/"),
)
# aider's own scratch artifacts (chat/input history, tags cache). Not code — must
# never count as an edit, land on the branch, or trip the scope predicate.
_AIDER_ARTIFACT = re.compile(r"(^|/)\.aider")


@dataclass
class AiderResult:
    ticket_id: str
    model: str
    branch: str
    edited: bool = False
    changed_files: list[str] = field(default_factory=list)
    tests_green: bool | None = None          # None = not run (no test target)
    scope_blocked: bool = False
    scope_reasons: list[str] = field(default_factory=list)
    scope_warnings: list[str] = field(default_factory=list)
    gate_passed: bool = False
    wall_s: float = 0.0
    workdir: str = ""
    note: str = ""
    aider_tail: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def evaluate_diff_scope(changed_files, affected_files=None) -> dict:
    """Pure safety predicate over aider's changed-file set.

    BLOCK (hard) if any changed path is a test file or a protected infra path or
    escapes the repo (absolute / parent-traversal). WARN (advisory) if a changed
    path is outside the ticket's declared affected-files list — the list is prose
    and unreliable, so it never blocks (advisor 2026-07-06).

    Returns {blocked: bool, reasons: [str], warnings: [str]}. Deterministic and
    dependency-free so it red->greens without aider or git.
    """
    reasons: list[str] = []
    warnings: list[str] = []
    changed = [c.replace("\\", "/").strip() for c in (changed_files or []) if c.strip()]

    for path in changed:
        if any(p.search(path) for p in _TEST_PATTERNS):
            reasons.append(f"edited test file: {path}")
        if any(p.search(path) for p in _PROTECTED_PATTERNS):
            reasons.append(f"edited protected path: {path}")
        if path.startswith("/") or path.startswith("..") or "/../" in path:
            reasons.append(f"path escapes repo: {path}")

    if affected_files:
        allow = {a.replace("\\", "/").strip() for a in affected_files if a.strip()}
        for path in changed:
            if path not in allow:
                warnings.append(f"outside declared affected-files: {path}")

    return {"blocked": bool(reasons), "reasons": reasons, "warnings": warnings}


def _git(args, cwd) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )


def _clone_repo(source: Path, dest: Path) -> None:
    """Clone source repo to dest (throwaway). Local clone if source is a git repo,
    else a plain copytree (so non-git task-packs still work)."""
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if (source / ".git").exists():
        r = _git(["clone", "--quiet", str(source), str(dest)], cwd=source.parent)
        if r.returncode != 0:
            raise RuntimeError(f"git clone failed: {r.stderr.strip()[:300]}")
    else:
        shutil.copytree(source, dest)
        _git(["init", "--quiet"], cwd=dest)
        _git(["add", "-A"], cwd=dest)
        _git(["commit", "--quiet", "-m", "runner: baseline"], cwd=dest)
    _git(["config", "user.email", "aider-device@local"], cwd=dest)
    _git(["config", "user.name", "aider-device"], cwd=dest)
    # Keep aider's scratch out of the clone's git without touching the source repo's
    # tracked .gitignore (info/exclude is local to this clone).
    exclude = dest / ".git" / "info" / "exclude"
    if exclude.parent.exists():
        exclude.write_text(".aider*\n")


def _changed_files(workdir: Path) -> list[str]:
    """Stage everything and return the repo-relative changed-file set (incl. new)."""
    _git(["add", "-A"], cwd=workdir)
    r = _git(["diff", "--cached", "--name-only"], cwd=workdir)
    return [ln.strip() for ln in r.stdout.splitlines()
            if ln.strip() and not _AIDER_ARTIFACT.search(ln.strip())]


def _run_aider(workdir: Path, message: str, model: str, add_files, map_tokens: int,
               timeout: int) -> tuple[float, str]:
    """Invoke aider headless in workdir. aider stays fully external (subprocess).
    Returns (wall_seconds, stdout_tail). --no-auto-commits: we own the commit so the
    gate runs on the working tree before anything lands on the branch."""
    env = dict(os.environ)
    env["OLLAMA_API_BASE"] = HEX_OLLAMA
    cmd = [
        str(aider_bin()),
        *[str(f) for f in (add_files or [])],   # pre-added files (else aider uses repo map)
        "--model", f"ollama_chat/{model}",
        "--message", message,
        "--yes-always",
        "--no-auto-commits",
        "--no-check-update",
        "--no-analytics",
        "--no-gitignore",
        "--map-tokens", str(map_tokens),
    ]
    t0 = time.monotonic()
    try:
        r = subprocess.run(cmd, cwd=str(workdir), env=env, capture_output=True,
                           text=True, timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
    except subprocess.TimeoutExpired:
        out = f"aider TIMED OUT after {timeout}s"
    dt = time.monotonic() - t0
    tail = "\n".join(out.splitlines()[-25:])
    return dt, tail


def _run_tests(workdir: Path, test_paths) -> tuple[bool | None, str]:
    """Run pytest against the given repo-relative test paths inside the clone.

    Returns (green|None, tail). None when no target given — the gate then cannot
    assert correctness and the caller escalates to CC (honest: no silent green).
    PYTHONPATH is prepended with the clone so `import`s resolve to the EDITED code,
    not an editable install pointing at the original working tree (contamination guard).
    """
    if not test_paths:
        return None, "(no test target — correctness not asserted)"
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(workdir), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=line", *test_paths],
        cwd=str(workdir), env=env, capture_output=True, text=True,
    )
    out = (r.stdout + r.stderr).strip()
    tail = out.splitlines()[-1] if out else "(no output)"
    return r.returncode == 0, tail


def build(ticket_id: str, repo_source, message: str, *, model: str = DEFAULT_MODEL,
          add_files=None, test_paths=None, affected_files=None, branch: str = "",
          workdir=None, map_tokens: int = 1024, timeout: int = 900) -> AiderResult:
    """Clone -> branch -> aider -> gate -> commit. Returns an AiderResult.

    gate_passed is TRUE only when aider edited files, tests are green, and the
    diff-scope predicate did not block. Anything else -> the caller escalates.
    """
    source = Path(repo_source).expanduser().resolve()
    branch = branch or f"aider/{ticket_id}-{int(time.time())}"
    work = Path(workdir).expanduser() if workdir else (
        Path(os.environ.get("AIDER_WORKROOT", str(Path.home() / ".unseen_university" / "aider_work")))
        / f"{ticket_id}-{int(time.time())}"
    )
    res = AiderResult(ticket_id=ticket_id, model=model, branch=branch, workdir=str(work))

    _clone_repo(source, work)
    r = _git(["checkout", "-b", branch], cwd=work)
    if r.returncode != 0:
        res.note = f"branch create failed: {r.stderr.strip()[:200]}"
        return res

    res.wall_s, res.aider_tail = _run_aider(work, message, model, add_files, map_tokens, timeout)
    res.changed_files = _changed_files(work)
    res.edited = bool(res.changed_files)

    scope = evaluate_diff_scope(res.changed_files, affected_files)
    res.scope_blocked = scope["blocked"]
    res.scope_reasons = scope["reasons"]
    res.scope_warnings = scope["warnings"]

    res.tests_green, test_tail = _run_tests(work, test_paths)

    # Commit whatever aider produced to the branch — evidence survives a failed gate
    # so CC can inspect. Never touches the source repo or its main.
    if res.edited:
        _git(["commit", "--quiet", "-m",
              f"aider[{model}]: {ticket_id} (gate pending)"], cwd=work)

    res.gate_passed = bool(res.edited and res.tests_green and not res.scope_blocked)
    res.note = (
        f"edited={res.edited} tests_green={res.tests_green} "
        f"scope_blocked={res.scope_blocked} :: {test_tail}"
    )
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description="Standalone headless aider build runner")
    ap.add_argument("--repo", required=True, help="source repo to clone (throwaway)")
    ap.add_argument("--message", required=True, help="the coding task for aider")
    ap.add_argument("--ticket", default="adhoc", help="ticket id for branch/labels")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--test", action="append", default=[],
                    help="repo-relative test path(s); repeatable")
    ap.add_argument("--file", action="append", default=[],
                    help="file(s) to pre-add to the aider chat; repeatable")
    ap.add_argument("--affected", action="append", default=[],
                    help="declared affected file(s) for the advisory scope warn; repeatable")
    ap.add_argument("--map-tokens", type=int, default=1024)
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    res = build(
        args.ticket, args.repo, args.message, model=args.model,
        add_files=args.file or None, test_paths=args.test or None,
        affected_files=args.affected or None, map_tokens=args.map_tokens,
        timeout=args.timeout,
    )
    print(f"== aider runner :: ticket={res.ticket_id} model={res.model} Hex={HEX_OLLAMA} ==")
    print(f"[branch]   {res.branch}")
    print(f"[workdir]  {res.workdir}")
    print(f"[wall]     {res.wall_s:.1f}s")
    print(f"[edited]   {res.edited}  files={res.changed_files}")
    print(f"[tests]    green={res.tests_green}")
    print(f"[scope]    blocked={res.scope_blocked} reasons={res.scope_reasons} warnings={res.scope_warnings}")
    print(f"[GATE]     {'PASS' if res.gate_passed else 'FAIL'} — {res.note}")
    sys.exit(0 if res.gate_passed else 1)


if __name__ == "__main__":
    main()
