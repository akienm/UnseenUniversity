"""Discriminating test for T-memory-emit-frozen-root-post-import.

memory_emit must honor UU_MEMORY_ROOT set at ANY time, not only at import. The bug:
MEMORY_ROOT is captured as a module-level constant at import (memory_emit.py:38), so a
later env change is ignored — emissions land in the frozen default while every other
reader (unseen_university.memory_root, proof_store, ticket_store) resolves the root
LIVE. That silently splits the store (found 2026-07-07: a test set UU_MEMORY_ROOT after
importing memory_emit and its proofs leaked into the REAL store).

RED (current, frozen): emit() writes under the import-time MEMORY_ROOT, ignoring the
later env change. GREEN (fixed, live): emit() resolves the root at call time and writes
under the current UU_MEMORY_ROOT.

The test simulates the freeze (patch MEMORY_ROOT to a 'frozen' tmp) then changes the env
to a 'live' tmp AFTER — so it never touches the real store regardless of outcome.
"""

from devlab.claudecode import memory_emit


def test_emit_honors_uu_memory_root_set_after_import(tmp_path, monkeypatch):
    frozen = tmp_path / "frozen"
    live = tmp_path / "live"
    # Simulate the import-time capture landing on 'frozen'...
    monkeypatch.setattr(memory_emit, "MEMORY_ROOT", str(frozen))
    # ...then the environment changes to 'live' AFTER import (the real-world case).
    monkeypatch.setenv("UU_MEMORY_ROOT", str(live))

    path = memory_emit.emit("notes", "test-frozen-root", {"text": "probe"})

    # A live-resolving emit writes under 'live'; the frozen bug writes under 'frozen'.
    assert str(live) in path, (
        f"emit ignored UU_MEMORY_ROOT set after import — wrote to {path!r} "
        "(frozen MEMORY_ROOT), splitting the store"
    )
    assert str(frozen) not in path
