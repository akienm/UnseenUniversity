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
    assert set(rec["links"]) == {"decisions", "tickets", "commits", "whys"}
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


def test_write_is_churn_free_noop(_tmp_root):
    import os as _os
    p1 = ts.write(_mk("T-noop"))
    mtime1 = _os.path.getmtime(p1)
    # writing the identical body back is a no-op: no rewrite, no stamp, file untouched
    p2 = ts.write(ts.read("T-noop"))
    assert p2 == p1
    assert _os.path.getmtime(p2) == mtime1


def test_write_does_not_stamp_updated_at(_tmp_root):
    # write() is a pure persist; only the granular mutators stamp updated_at
    ts.write(_mk("T-nostamp"))  # body has no updated_at
    assert "updated_at" not in ts.read("T-nostamp")
    ts.set_worker("T-nostamp", "CC.1")  # this one stamps
    assert ts.read("T-nostamp")["updated_at"]


def test_write_terminal_status_routes_to_closed(_tmp_root):
    ts.write(_mk("T-term", status="done"))
    assert len(_files_for(_tmp_root, "T-term")) == 0                 # not in active
    assert len(_files_for(_tmp_root, "T-term", closed=True)) == 1    # routed to closed/
    body = ts.read("T-term")
    assert body["status"] == "done"
    assert body["completed_at"]                                       # stamped on terminalize


def test_write_terminalizing_existing_moves_to_closed(_tmp_root):
    ts.write(_mk("T-mv", status="sprint"))
    assert len(_files_for(_tmp_root, "T-mv")) == 1
    body = ts.read("T-mv")
    body["status"] = "done"
    ts.write(body)
    assert len(_files_for(_tmp_root, "T-mv")) == 0
    assert len(_files_for(_tmp_root, "T-mv", closed=True)) == 1


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


# ── conditional_update (slice d: race-safe check-and-set) ──────────────────────


def test_conditional_update_path_on_match(_tmp_root):
    ts.write(_mk("T-cu", status="sprint"))

    def _mut(b):
        b["status"] = "in_progress"
        b["dispatched_at"] = "now"
        return b

    p = ts.conditional_update("T-cu", expect_current="sprint", mutate=_mut)
    assert p is not None
    got = ts.read("T-cu")
    assert got["status"] == "in_progress"
    assert got["dispatched_at"] == "now"


def test_conditional_update_none_on_mismatch_no_write(_tmp_root):
    ts.write(_mk("T-cu2", status="in_progress"))
    before = _files_for(_tmp_root, "T-cu2")[0].read_text()

    called = {"n": 0}

    def _mut(b):
        called["n"] += 1   # must NOT run when precondition fails
        b["status"] = "sprint"
        return b

    out = ts.conditional_update("T-cu2", expect_current="sprint", mutate=_mut)
    assert out is None
    assert called["n"] == 0
    # body unchanged on disk (modulo nothing — no write happened at all)
    assert _files_for(_tmp_root, "T-cu2")[0].read_text() == before


def test_conditional_update_keyerror_on_missing(_tmp_root):
    with pytest.raises(KeyError):
        ts.conditional_update("T-nope", expect_current="sprint", mutate=lambda b: b)


def test_conditional_update_accepts_status_set(_tmp_root):
    ts.write(_mk("T-cu3", status="in_progress"))
    p = ts.conditional_update(
        "T-cu3", expect_current={"sprint", "in_progress"},
        mutate=lambda b: {**b, "worker": "ds.0"})
    assert p is not None
    assert ts.read("T-cu3")["worker"] == "ds.0"


def test_conditional_update_only_one_wins(_tmp_root):
    """Two sequential claims with the same precondition: the first transitions
    sprint->in_progress, the second sees in_progress != sprint and returns None.
    This is the claim-race gate (only one worker wins) made deterministic."""
    ts.write(_mk("T-claim", status="sprint"))
    claim = lambda: ts.conditional_update(
        "T-claim", expect_current="sprint",
        mutate=lambda b: {**b, "status": "in_progress"})
    first = claim()
    second = claim()
    assert first is not None and second is None


# ── forensic trace (slice: chokepoint owns queue forensics) ───────────────────


def _traces(trace_dir):
    recs = []
    for p in Path(trace_dir).glob("*.jsonl"):
        for line in p.read_text().splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return recs


def test_forensic_trace_on_transitions(tmp_path, monkeypatch):
    trace_dir = tmp_path / "trace"
    monkeypatch.setenv("UU_QUEUE_TRACE_DIR", str(trace_dir))
    ts.write(_mk("T-fx", status="sprint"))             # ticket_created
    ts.set_status("T-fx", "assigned")                  # status_transition
    ts.conditional_update("T-fx", expect_current="assigned",
                          mutate=lambda b: {**b, "status": "in_progress"})  # transition
    ts.close("T-fx", result="done")                    # ticket_closed + ticket_moved

    recs = _traces(trace_dir)
    events = [r["event"] for r in recs]
    assert "ticket_created" in events
    assert "status_transition" in events
    assert "ticket_closed" in events
    assert "ticket_moved" in events
    # schema parity with DiagnosticBase.trace_record
    for r in recs:
        assert set(r) >= {"ts", "device", "event"}
        assert r["device"] == "queue"


def test_forensic_never_raises_on_bad_dir(tmp_path, monkeypatch):
    # point trace at a path that can't be created (a file, not a dir) → swallowed
    bad = tmp_path / "afile"
    bad.write_text("x")
    monkeypatch.setenv("UU_QUEUE_TRACE_DIR", str(bad / "sub"))
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    (tmp_path / "tickets").mkdir(exist_ok=True)
    # must not raise even though the trace write fails
    ts.write(_mk("T-safe"))
    assert ts.read("T-safe") is not None
