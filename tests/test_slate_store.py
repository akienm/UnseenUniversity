"""slate_store — canonical slate-path resolver (T-slate-location-canonical-devlab).

Slates moved out of ~/.unseen_university/claudecode/ into the dev-process memory
store at <repo>/devlab/runtime/memory/slates/. These pin the resolver so a
regression back to the old location, or a broken date normalization, fails loudly.
"""
import os
from pathlib import Path

from unseen_university import slate_store
from unseen_university.memory_root import memory_root


def test_slates_dir_is_under_memory_store():
    d = slate_store.slates_dir()
    assert d == memory_root() / "slates"
    # The whole point of the migration: NOT the old runtime-data location.
    assert "claudecode" not in str(d)
    assert d.parts[-3:] == ("runtime", "memory", "slates") or d.parent.name == "memory"


def test_today_slate_path_shape():
    p = slate_store.today_slate_path()
    assert p.parent == slate_store.slates_dir()
    assert p.name.endswith(".slate.txt")
    stamp = p.name[: -len(".slate.txt")]
    assert stamp.isdigit() and len(stamp) == 8  # YYYYMMDD


def test_slate_path_normalizes_dashes():
    # Accepts YYYY-MM-DD and YYYYMMDD; both resolve to the same dateless-dash file.
    assert slate_store.slate_path("2026-06-22").name == "20260622.slate.txt"
    assert slate_store.slate_path("20260622").name == "20260622.slate.txt"
    assert slate_store.slate_path("2026-06-22") == slate_store.slate_path("20260622")


def test_uu_memory_root_override(tmp_path, monkeypatch):
    # The resolver honors UU_MEMORY_ROOT so it relocates with ticket_store/proof_store.
    monkeypatch.setenv("UU_MEMORY_ROOT", str(tmp_path))
    assert slate_store.slates_dir() == tmp_path / "slates"
    assert slate_store.today_slate_path().parent == tmp_path / "slates"
