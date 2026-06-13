"""
repo_auditor.py — Structural audit of closed tickets against the git repo.

Three structural signals per ticket:
  1. NO_COMMIT   — git log --all --grep=<ticket_id> returns nothing → HIGH
  2. FILE_OVERLAP — Affected files in description have zero overlap with actual
                    diff files → MED (only when list is specific, not TBD)
  3. DIFF_MAGNITUDE — L/XL with < 20 lines changed, M with < 5 lines → LOW

Entry point:
  run_structural_audit(repo_path='.', size_filter=['M','L','XL']) -> list[AuditFlag]

Flags are written to ~/.unseen_university/hubert/audit_flags.jsonl.
Re-running is idempotent: same (ticket_id, signal) pair → upsert in place.

Skip conditions:
  - Tags contain Tracking, Decision, or Doc (coordination-only tickets)
  - ticket id does not start with 'T-' (predates T-id convention)
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_IGOR_HOME = Path(os.environ.get("IGOR_HOME", Path.home() / ".unseen_university"))
_FLAGS_FILE = _IGOR_HOME / "hubert" / "audit_flags.jsonl"
_SKIP_TAGS = {"tracking", "decision", "doc"}

# Minimum changed-line counts by ticket size
_SIZE_MIN_LINES = {"M": 5, "L": 20, "XL": 20}


@dataclass
class AuditFlag:
    """One structural audit finding."""
    ticket_id: str
    signal: str      # "NO_COMMIT" | "FILE_OVERLAP" | "DIFF_MAGNITUDE"
    severity: str    # "HIGH" | "MED" | "LOW"
    detail: str
    checked_at: str  # ISO timestamp


# ── Signal helpers ────────────────────────────────────────────────────────────

def _git_commits_for_ticket(ticket_id: str, repo_path: Path) -> list[str]:
    """Return commit hashes mentioning ticket_id (from git log --all --grep)."""
    try:
        result = subprocess.run(
            ["git", "log", "--all", "--grep", ticket_id, "--format=%H"],
            capture_output=True, text=True, timeout=15, cwd=repo_path,
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception as exc:
        log.warning("repo_auditor: git log failed for %s: %s", ticket_id, exc)
        return []


def _git_changed_files(commit_hashes: list[str], repo_path: Path) -> set[str]:
    """Return the set of files changed across all commits for a ticket."""
    changed: set[str] = set()
    for h in commit_hashes:
        try:
            result = subprocess.run(
                ["git", "show", "--stat", "--name-only", "--format=", h],
                capture_output=True, text=True, timeout=15, cwd=repo_path,
            )
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith(" ") and not stripped.startswith("|"):
                    changed.add(stripped)
        except Exception as exc:
            log.warning("repo_auditor: git show failed for %s: %s", h, exc)
    return changed


def _git_lines_changed(commit_hashes: list[str], repo_path: Path) -> int:
    """Return total lines changed (insertions + deletions) across commits."""
    total = 0
    for h in commit_hashes:
        try:
            result = subprocess.run(
                ["git", "show", "--stat", h],
                capture_output=True, text=True, timeout=15, cwd=repo_path,
            )
            # Last line: "N files changed, M insertions(+), K deletions(-)"
            for line in result.stdout.splitlines():
                m = re.search(r"(\d+) insertion", line)
                if m:
                    total += int(m.group(1))
                m = re.search(r"(\d+) deletion", line)
                if m:
                    total += int(m.group(1))
        except Exception as exc:
            log.warning("repo_auditor: git stat failed for %s: %s", h, exc)
    return total


def _parse_affected_files(description: str) -> list[str] | None:
    """
    Extract the Affected files list from a ticket description.
    Returns None when absent, empty, TBD, or contains only vague phrases.
    Returns a list of normalized path strings when specific files are named.
    """
    m = re.search(
        r"\*\*Affected files:\*\*(.*?)(?=\n\*\*[A-Za-z]|\Z)",
        description or "",
        re.DOTALL | re.IGNORECASE,
    )
    if not m:
        return None

    raw = m.group(1).strip()
    if not raw or re.search(r"\bTBD\b|\bdiscovery step\b|\bnone apply\b", raw, re.IGNORECASE):
        return None

    # Split on commas, newlines, semicolons; strip bullets, parens notes
    parts = re.split(r"[,\n;]", raw)
    paths: list[str] = []
    for part in parts:
        # Strip markdown bullets and parenthetical notes like "(new)", "(read only)"
        clean = re.sub(r"\(.*?\)", "", part)
        clean = clean.strip().lstrip("*-• ")
        # Accept if it looks like a file path (contains . or /)
        if clean and ("." in clean or "/" in clean):
            paths.append(clean.strip())

    return paths if paths else None


# ── Flag writing ──────────────────────────────────────────────────────────────

def _read_existing_flags() -> dict[tuple[str, str], dict]:
    """Load existing flags as {(ticket_id, signal): flag_dict} for upsert."""
    index: dict[tuple[str, str], dict] = {}
    if not _FLAGS_FILE.exists():
        return index
    try:
        for line in _FLAGS_FILE.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                key = (entry["ticket_id"], entry["signal"])
                index[key] = entry
            except (json.JSONDecodeError, KeyError):
                pass
    except Exception as exc:
        log.warning("repo_auditor: could not read flags file: %s", exc)
    return index


def _write_flags(flags_index: dict[tuple[str, str], dict]) -> None:
    """Write all flags back to the JSONL file (full rewrite for upsert)."""
    _FLAGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        _FLAGS_FILE.write_text(
            "\n".join(json.dumps(f) for f in flags_index.values()) + "\n"
            if flags_index else ""
        )
    except Exception as exc:
        log.error("repo_auditor: could not write flags: %s", exc)


def _upsert_flag(flag: AuditFlag) -> None:
    """Idempotent write: insert or update (ticket_id, signal) entry."""
    index = _read_existing_flags()
    key = (flag.ticket_id, flag.signal)
    index[key] = asdict(flag)
    _write_flags(index)


# ── Per-ticket audit ──────────────────────────────────────────────────────────

def _audit_ticket(ticket: dict, repo_path: Path) -> list[AuditFlag]:
    """Run all three structural signals for one ticket. Returns new flags."""
    tid = ticket.get("id", "")
    size = (ticket.get("size") or "").upper()
    description = ticket.get("description") or ""
    now_iso = datetime.now(timezone.utc).isoformat()
    flags: list[AuditFlag] = []

    commits = _git_commits_for_ticket(tid, repo_path)
    log.info("repo_auditor: %s — %d commits found", tid, len(commits))

    # Signal 1: NO_COMMIT
    if not commits:
        flags.append(AuditFlag(
            ticket_id=tid,
            signal="NO_COMMIT",
            severity="HIGH",
            detail=f"git log --all --grep={tid} returned no results",
            checked_at=now_iso,
        ))
        # Can't check file overlap or diff magnitude without commits
        return flags

    # Signal 2: FILE_OVERLAP
    affected = _parse_affected_files(description)
    if affected:
        changed = _git_changed_files(commits, repo_path)
        # Normalize affected paths for comparison (strip leading ./)
        affected_norm = {p.lstrip("./").split(" ")[0] for p in affected if p}
        overlap = affected_norm & changed
        if not overlap:
            flags.append(AuditFlag(
                ticket_id=tid,
                signal="FILE_OVERLAP",
                severity="MED",
                detail=(
                    f"Described files: {sorted(affected_norm)} — "
                    f"actual changed files: {sorted(changed)[:5]} — no overlap"
                ),
                checked_at=now_iso,
            ))

    # Signal 3: DIFF_MAGNITUDE
    min_lines = _SIZE_MIN_LINES.get(size)
    if min_lines:
        lines = _git_lines_changed(commits, repo_path)
        if lines < min_lines:
            flags.append(AuditFlag(
                ticket_id=tid,
                signal="DIFF_MAGNITUDE",
                severity="LOW",
                detail=(
                    f"Ticket size={size} but only {lines} lines changed "
                    f"(threshold: {min_lines})"
                ),
                checked_at=now_iso,
            ))

    return flags


# ── Entry point ───────────────────────────────────────────────────────────────

def run_structural_audit(
    repo_path: str = ".",
    size_filter: list[str] | None = None,
) -> list[AuditFlag]:
    """
    Run structural audit across closed tickets. Returns list of AuditFlag.
    Writes flags to audit_flags.jsonl (idempotent upsert).

    Args:
        repo_path: git repo root (default: current directory)
        size_filter: ticket sizes to include (default: M, L, XL)
    """
    from lab.claudecode.completion_audit import get_closed_tickets

    if size_filter is None:
        size_filter = ["M", "L", "XL"]
    size_set = {s.upper() for s in size_filter}
    repo = Path(repo_path).resolve()

    tickets = get_closed_tickets(days=90)
    log.info(
        "repo_auditor: auditing %d closed tickets (filter=%s)",
        len(tickets), size_set,
    )

    all_flags: list[AuditFlag] = []
    for ticket in tickets:
        tid = ticket.get("id", "")
        if not tid.startswith("T-"):
            continue

        size = (ticket.get("size") or "").upper()
        if size not in size_set:
            continue

        tags = {(t or "").lower() for t in (ticket.get("tags") or [])}
        if tags & _SKIP_TAGS:
            log.debug("repo_auditor: skipping coordination ticket %s (tags=%s)", tid, tags)
            continue

        try:
            flags = _audit_ticket(ticket, repo)
            for flag in flags:
                _upsert_flag(flag)
                log.info(
                    "repo_auditor: FLAG|ticket=%s|signal=%s|severity=%s",
                    flag.ticket_id, flag.signal, flag.severity,
                )
            all_flags.extend(flags)
        except Exception as exc:
            log.warning("repo_auditor: error auditing %s: %s", tid, exc)

    log.info("repo_auditor: complete — %d flags from %d tickets", len(all_flags), len(tickets))
    return all_flags


def read_flags(ticket_id: str | None = None) -> list[dict]:
    """Read persisted audit flags; optionally filter by ticket_id."""
    index = _read_existing_flags()
    if ticket_id:
        return [f for f in index.values() if f.get("ticket_id") == ticket_id]
    return list(index.values())
