"""
pipeline_manager.py — D096: Pipeline and job state via filesystem convention.

Convention:
  - State is represented by a file at:
      ~/.unseen_university/{instance}/{category}/{name}/{state}.now   (transient/active)
      ~/.unseen_university/{instance}/{category}/{name}/{state}.txt   (terminal/permanent)

  - File mtime IS the timestamp — no redundant timestamp field in the contents.
  - Only one .now file exists per entry at a time (old .now removed on transition).
  - .txt files accumulate (permanent record: done.txt, failed.txt, etc.).

  Categories: "pipelines", "jobs", or any other grouping.

Usage:
  write_state("pipelines", "book_ingest", "running", {"book": "On Intelligence"})
  write_state("pipelines", "book_ingest", "done")   # mtime = completion time
  state = get_state("pipelines", "book_ingest")     # {"state": "done", "mtime": ..., "contents": ...}
  list_states("pipelines")                           # all entries + current state

instance_id from IGOR_INSTANCE_ID env (defaults to "Igor-wild-0001").
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..paths import paths

_BASE = paths().instance

# Terminal states — written as .txt (permanent), never removed
_TERMINAL = {"done", "failed", "cancelled", "skipped"}


def _entry_dir(category: str, name: str) -> Path:
    d = _BASE / category / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_state(
    category: str,
    name: str,
    state: str,
    contents: Optional[dict] = None,
) -> Path:
    """
    Write a state file for the given entry.

    - Transient states (anything not in _TERMINAL): written as {state}.now.
      Any existing .now file is removed first (one active state at a time).
    - Terminal states (done/failed/cancelled/skipped): written as {state}.txt.
      Never removed — they form the permanent audit trail.

    contents: optional dict written as JSON in the file body.
    Returns: path of the written file.
    """
    d = _entry_dir(category, name)
    ext = ".txt" if state in _TERMINAL else ".now"
    target = d / f"{state}{ext}"

    # Remove old .now file(s) on transition (both transient→transient and transient→terminal)
    for old in d.glob("*.now"):
        if old != target:
            old.unlink(missing_ok=True)

    body = ""
    if contents:
        body = json.dumps(contents, indent=2)

    target.write_text(body, encoding="utf-8")
    return target


def get_state(category: str, name: str) -> dict:
    """
    Return the current state of an entry.

    Returns dict:
      {
        "state": str,          # state name (stem of most recent file)
        "mtime": str,          # ISO8601 mtime of the state file
        "contents": dict|None, # parsed JSON from file body, or None if empty
        "terminal": bool,      # True if state is a terminal state
      }

    Priority: .now file wins (active); if none, latest .txt by mtime.
    Returns {"state": None, ...} if no state files exist.
    """
    d = _entry_dir(category, name)

    # Active .now file takes priority
    now_files = list(d.glob("*.now"))
    if now_files:
        f = now_files[0]  # there should only be one
    else:
        txt_files = list(d.glob("*.txt"))
        if not txt_files:
            return {"state": None, "mtime": None, "contents": None, "terminal": False}
        f = max(txt_files, key=lambda p: p.stat().st_mtime)

    state_name = f.stem
    mtime = datetime.fromtimestamp(f.stat().st_mtime).isoformat()
    body = f.read_text(encoding="utf-8").strip()
    contents = None
    if body:
        try:
            contents = json.loads(body)
        except json.JSONDecodeError:
            contents = {"raw": body}

    return {
        "state": state_name,
        "mtime": mtime,
        "contents": contents,
        "terminal": state_name in _TERMINAL,
    }


def list_states(category: str) -> list[dict]:
    """
    List all named entries in a category with their current state.

    Returns list of dicts:
      [{"name": str, "state": str, "mtime": str, "terminal": bool}, ...]
    Sorted by mtime descending (most recently changed first).
    """
    cat_dir = _BASE / category
    if not cat_dir.exists():
        return []

    results = []
    for entry_dir in sorted(cat_dir.iterdir()):
        if not entry_dir.is_dir():
            continue
        info = get_state(category, entry_dir.name)
        if info["state"] is not None:
            results.append(
                {
                    "name": entry_dir.name,
                    "state": info["state"],
                    "mtime": info["mtime"],
                    "terminal": info["terminal"],
                }
            )

    results.sort(key=lambda r: r["mtime"] or "", reverse=True)
    return results


def clear_state(category: str, name: str) -> int:
    """Remove all state files for an entry. Returns count removed."""
    d = _entry_dir(category, name)
    removed = 0
    for f in list(d.glob("*.now")) + list(d.glob("*.txt")):
        f.unlink(missing_ok=True)
        removed += 1
    return removed
