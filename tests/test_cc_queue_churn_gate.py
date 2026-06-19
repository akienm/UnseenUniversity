"""Churn gate for the cc_queue filesystem-first cutover — #3 slice (b)+(c).

D-build-queue-filesystem-first-2026-06-19 / T-cc-queue-fs-first. _load() backfills
9 default keys (role/priority/gate/...) in-memory; 66 of 124 live tickets lack
>=1 of them on disk. A naive _save would rewrite the in-memory (backfilled) body
for every such ticket — a ~66-file churn bomb on every queue command. _save's
change-detect normalizes the disk body with _apply_load_defaults before comparing,
so an unmutated round-trip writes NOTHING.

This gate pins BOTH directions:
  (-) load-all -> save-all with no mutation writes 0 files (no churn from backfill);
  (+) mutate one ticket -> save writes EXACTLY that one, and the change lands on disk.

PG is isolated (monkeypatched out) so this exercises only the filesystem path.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "devlab", "claudecode"))

import cc_queue  # noqa: E402
from unseen_university import ticket_store as ts  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_root(tmp_path, monkeypatch):
    """Temp filesystem store; PG fully isolated so only the FS path is tested."""
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    (tmp_path / "tickets").mkdir(parents=True, exist_ok=True)
    # Isolate Postgres: no PG-only fold-in on load, no mirror on save.
    monkeypatch.setattr(cc_queue, "_load_pg", lambda: [])
    monkeypatch.setattr(cc_queue, "_save_pg_mirror", lambda tasks: None)
    yield tmp_path


def _seed_default_missing(tid, **kw):
    """Write a ticket that deliberately OMITS the load-default keys.

    Reproduces the 66-default-missing condition: only the minimal fields land on
    disk, so _load's backfill materializes role/priority/gate/... in-memory.
    """
    body = {"id": tid, "title": f"title {tid}", "status": "sprint",
            "worker": None, "created_by": "cc.0"}
    body.update(kw)
    # Sanity: the seed must actually be missing defaults or the gate is vacuous.
    for k in ("role", "gate", "github_issue", "dispatched_at"):
        assert k not in body, f"seed must omit default key {k!r}"
    ts.write(body)


def _count_writes(monkeypatch):
    """Patch ticket_store._atomic_write to count actual file writes."""
    calls = {"n": 0}
    real = ts._atomic_write

    def _spy(path, record):
        calls["n"] += 1
        return real(path, record)

    monkeypatch.setattr(ts, "_atomic_write", _spy)
    return calls


def test_round_trip_no_mutation_writes_zero(monkeypatch):
    """The churn gate: load-all -> save-all with no change rewrites NOTHING."""
    for i in range(5):
        _seed_default_missing(f"T-churn-{i}")

    tasks = cc_queue._load()
    assert len(tasks) == 5
    # _load backfilled defaults in-memory — prove the precondition is real.
    assert any("role" in t for t in tasks)

    calls = _count_writes(monkeypatch)
    cc_queue._save(tasks)
    assert calls["n"] == 0, (
        f"churn bomb: {calls['n']} files rewritten on a no-op save — the "
        "default backfill is masquerading as a change"
    )


def test_single_mutation_writes_exactly_one(monkeypatch):
    """The other direction: a genuine change writes exactly one file, and lands."""
    for i in range(5):
        _seed_default_missing(f"T-mut-{i}")

    tasks = cc_queue._load()
    target = next(t for t in tasks if t["id"] == "T-mut-2")
    target["status"] = "in_progress"

    calls = _count_writes(monkeypatch)
    cc_queue._save(tasks)
    assert calls["n"] == 1, f"expected exactly 1 write, got {calls['n']}"

    # The change is durable on disk; the other four are untouched.
    assert ts.read("T-mut-2")["status"] == "in_progress"
    assert ts.read("T-mut-0")["status"] == "sprint"
