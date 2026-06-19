"""Unit tests for unseen_university/ticket_store.py — the filesystem queue chokepoint.

D-build-queue-filesystem-first-2026-06-19 / T-ticket-store-module. Proves the store
reads/writes/lists/closes purely on the filesystem with no DB, and that the close
move + cross-process concurrency leave a consistent single-copy terminal state.
"""

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from unseen_university import ticket_store as ts


@pytest.fixture(autouse=True)
def _tmp_root(tmp_path, monkeypatch):
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    (tmp_path / "tickets").mkdir(parents=True, exist_ok=True)
    yield tmp_path


def _mk(tid, status="sprint", **kw):
    body = {
        "id": tid,
        "title": f"title {tid}",
        "status": status,
        "worker": None,
        "priority": kw.pop("priority", 0.5),
        "created_by": "cc.0",
    }
    body.update(kw)
    return body


def _files_for(root, tid, closed=False):
    d = Path(root) / "tickets" / ("closed" if closed else "")
    if not d.exists():
        return []
    return [p for p in d.glob("*.json") if json.loads(p.read_text())["body"]["id"] == tid]


# ── basic API ─────────────────────────────────────────────────────────────────


def test_write_read_roundtrip():
    ts.write(_mk("T-a", title="alpha"))
    got = ts.read("T-a")
    assert got is not None
    assert got["id"] == "T-a"
    assert got["status"] == "sprint"
    assert got["title"] == "alpha"
    assert ts.read("T-missing") is None


def test_envelope_schema_matches_memory_emit(_tmp_root):
    ts.write(_mk("T-env"))
    f = _files_for(_tmp_root, "T-env")[0]
    rec = json.loads(f.read_text())
    # byte-compatible top-level envelope keys
    assert set(rec) == {"id", "emitter", "namespace", "category", "kind",
                        "emitted_at", "links", "body"}
    assert rec["category"] == "tickets"
    assert rec["kind"] == "ticket"
    assert rec["namespace"] == ["T-env"]
    assert set(rec["links"]) == {"goals", "decisions", "tickets", "commits", "whys"}
    assert rec["links"]["tickets"] == ["T-env"]


def test_update_in_place_no_duplicate_file(_tmp_root):
    ts.write(_mk("T-u"))
    ts.set_status("T-u", "in_progress")
    ts.set_worker("T-u", "CC.1")
    # exactly one active file for the id — updates rewrite in place, never accrete
    assert len(_files_for(_tmp_root, "T-u")) == 1
    got = ts.read("T-u")
    assert got["status"] == "in_progress"
    assert got["worker"] == "CC.1"
    assert got["updated_at"]


def test_list_filters_by_status_and_excludes_closed(_tmp_root):
    ts.write(_mk("T-1", "sprint"))
    ts.write(_mk("T-2", "in_progress"))
    ts.write(_mk("T-3", "sprint"))
    assert {b["id"] for b in ts.list(status_filter="sprint")} == {"T-1", "T-3"}
    assert len(ts.list()) == 3
    ts.close("T-2")
    assert {b["id"] for b in ts.list()} == {"T-1", "T-3"}            # in-flight only
    assert {b["id"] for b in ts.list(include_closed=True)} == {"T-1", "T-2", "T-3"}


# ── close / terminal semantics ──────────────────────────────────────────────


def test_close_moves_to_closed_atomically(_tmp_root):
    ts.write(_mk("T-c"))
    dest = ts.close("T-c", result="done it")
    assert len(_files_for(_tmp_root, "T-c")) == 0          # gone from active
    closed = _files_for(_tmp_root, "T-c", closed=True)
    assert len(closed) == 1                                 # present in closed/, single
    body = json.loads(closed[0].read_text())["body"]
    assert body["status"] == "closed"
    assert body["result"] == "done it"
    assert body["completed_at"]
    assert "closed" in dest.replace("\\", "/").split("/")
    # still findable; excluded from in-flight views
    assert ts.read("T-c")["status"] == "closed"
    assert all(b["id"] != "T-c" for b in ts.list())


def test_set_status_terminal_delegates_to_close(_tmp_root):
    ts.write(_mk("T-d"))
    ts.set_status("T-d", "done")
    assert len(_files_for(_tmp_root, "T-d")) == 0
    assert len(_files_for(_tmp_root, "T-d", closed=True)) == 1
    assert ts.read("T-d")["status"] == "done"


def test_close_rejects_nonterminal_status():
    ts.write(_mk("T-bad"))
    with pytest.raises(ValueError):
        ts.close("T-bad", status="sprint")


def test_mutation_on_terminal_ticket_is_ignored():
    ts.write(_mk("T-e"))
    ts.close("T-e")
    ts.set_worker("T-e", "CC.1")          # no-op on terminal
    ts.set_status("T-e", "in_progress")   # delegates? no — non-terminal path, ignored
    assert ts.read("T-e").get("worker") is None
    assert ts.read("T-e")["status"] == "closed"


def test_missing_ticket_mutations_raise():
    with pytest.raises(KeyError):
        ts.set_worker("T-nope", "CC.1")
    with pytest.raises(KeyError):
        ts.close("T-nope")


# ── next_for_worker ───────────────────────────────────────────────────────────


def test_next_for_worker_priority_and_worker_filter():
    ts.write(_mk("T-lo", "sprint", priority=0.2))
    ts.write(_mk("T-hi", "sprint", priority=0.9))
    ts.write(_mk("T-ip", "in_progress", priority=0.99))   # not workable
    assert ts.next_for_worker()["id"] == "T-hi"
    ts.set_worker("T-hi", "CC.1")
    # CC.0 can't take CC.1's ticket → falls to the unassigned lower-priority one
    assert ts.next_for_worker("CC.0")["id"] == "T-lo"


def test_next_for_worker_empty_returns_none():
    assert ts.next_for_worker() is None


# ── no Postgres dependency (pins the decision's measurement signal) ─────────────


def test_no_postgres_dependency_in_module_or_runtime():
    import ast
    src = Path(ts.__file__).read_text()
    # AST import check (not substring — the docstring legitimately *names* the
    # tables it refuses to read). No DB driver may be imported.
    imported = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(n.name.split(".")[0] for n in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "psycopg2" not in imported
    assert "sqlite3" not in imported
    # no SQL against the ticket tables anywhere in executable code
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            # skip the module docstring (the only place these names appear as prose)
            if "filesystem ticket-store" in low:
                continue
            assert "clan.memories" not in low and "devlab.tickets" not in low
    # all ops function with no DB reachable (they never open one)
    ts.write(_mk("T-nodb"))
    assert ts.read("T-nodb") is not None
    ts.close("T-nodb")
    assert ts.read("T-nodb")["status"] == "closed"


# ── cross-process concurrency: the real verification of flock + atomic move ─────


def _child_set_worker(root, tid):
    os.environ["UU_MEMORY_ROOT"] = root
    from unseen_university import ticket_store as t
    try:
        t.set_worker(tid, "CC.1")
    except KeyError:
        pass


def _child_close(root, tid):
    os.environ["UU_MEMORY_ROOT"] = root
    from unseen_university import ticket_store as t
    try:
        t.close(tid, result="raced")
    except KeyError:
        pass


def test_concurrent_set_worker_and_close_invariant(_tmp_root):
    """set_worker(W) || close() across two PROCESSES must leave exactly one copy,
    in closed/, terminal — no resurrected active copy, no duplicate. This is what
    the flock + atomic-rename guarantee; atomic write alone would not stop the
    resurrection dup, so this asserts the stronger post-race invariant."""
    root = str(_tmp_root)
    for i in range(10):
        tid = f"T-race{i}"
        ts.write(_mk(tid))
        p1 = multiprocessing.Process(target=_child_set_worker, args=(root, tid))
        p2 = multiprocessing.Process(target=_child_close, args=(root, tid))
        p1.start(); p2.start()
        p1.join(15); p2.join(15)
        assert not p1.is_alive() and not p2.is_alive(), "child process hung (possible deadlock)"
        active = _files_for(root, tid)
        closed = _files_for(root, tid, closed=True)
        assert len(active) == 0, f"{tid}: resurrected active copy after close"
        assert len(closed) == 1, f"{tid}: expected single closed copy, got {len(closed)}"
        body = json.loads(closed[0].read_text())["body"]
        assert body["status"] in ts.TERMINAL_STATUSES
