"""T-reasoning-cache-sweep — periodic sweep + size cap tests.

Pass-2 Area 3 Finding P4 (SHIP). reasoning_cache had no disk-hygiene path;
entries only got deleted when someone tried to read them after TTL. This
test pins the sweep behavior: stale entries deleted on sweep, directory
size capped at IGOR_REASONING_CACHE_MAX.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from unseen_university.devices.igor.cognition import reasoning_cache as rc


@pytest.fixture
def tmp_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(rc, "CACHE_DIR", tmp_path)
    yield tmp_path


def _write_entry(cache_dir: Path, name: str, age_seconds: float):
    """Write a fake entry and backdate its mtime by age_seconds."""
    path = cache_dir / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "response_text": "x",
                "ts": time.time() - age_seconds,
                "max_twm_id": 0,
                "model": "m",
            }
        )
    )
    # Backdate mtime so _sweep's stale_cutoff sees it
    mtime = time.time() - age_seconds
    import os

    os.utime(path, (mtime, mtime))
    return path


class TestSweep:
    def test_deletes_entries_older_than_2x_ttl(self, tmp_cache):
        stale = _write_entry(tmp_cache, "stale", rc.TTL_SECONDS * 3)
        fresh = _write_entry(tmp_cache, "fresh", 60)
        expired, cap = rc._sweep()
        assert expired == 1
        assert cap == 0
        assert not stale.exists()
        assert fresh.exists()

    def test_caps_directory_size(self, tmp_cache, monkeypatch):
        monkeypatch.setattr(rc, "_CACHE_MAX_FILES", 5)
        # Write 10 fresh files; sweep should evict 5 oldest
        for i in range(10):
            # slight mtime stagger so oldest-first is deterministic
            p = _write_entry(tmp_cache, f"fresh_{i:02d}", i * 10)
        expired, cap = rc._sweep()
        assert expired == 0
        assert cap == 5
        remaining = list(tmp_cache.iterdir())
        assert len(remaining) == 5

    def test_empty_dir_is_safe(self, tmp_cache):
        expired, cap = rc._sweep()
        assert expired == 0
        assert cap == 0

    def test_missing_dir_is_safe(self, monkeypatch, tmp_path):
        missing = tmp_path / "nope"
        monkeypatch.setattr(rc, "CACHE_DIR", missing)
        expired, cap = rc._sweep()
        assert expired == 0
        assert cap == 0


class TestPutTriggersSweep:
    def test_sweep_runs_after_every_N_puts(self, tmp_cache, monkeypatch):
        monkeypatch.setattr(rc, "_SWEEP_EVERY_N_PUTS", 3)
        rc._put_counter = 0

        # Pre-seed a stale file to verify the sweep actually ran
        stale = _write_entry(tmp_cache, "stale", rc.TTL_SECONDS * 3)

        # First two puts — sweep should NOT have fired yet; stale still present
        rc.put("model", "p1", "r1", 0)
        assert stale.exists()
        rc.put("model", "p2", "r2", 0)
        assert stale.exists()

        # Third put triggers sweep — stale should be gone
        rc.put("model", "p3", "r3", 0)
        assert not stale.exists()

    def test_sweep_disabled_when_N_zero(self, tmp_cache, monkeypatch):
        monkeypatch.setattr(rc, "_SWEEP_EVERY_N_PUTS", 0)
        rc._put_counter = 0
        stale = _write_entry(tmp_cache, "stale", rc.TTL_SECONDS * 3)
        for _ in range(10):
            rc.put("model", f"p{_}", "r", 0)
        # Sweep never fires → stale persists
        assert stale.exists()
