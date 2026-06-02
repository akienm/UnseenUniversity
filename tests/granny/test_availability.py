"""Unit tests for devices.granny.availability — worker availability semaphore protocol."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture()
def avail_dir(tmp_path, monkeypatch):
    """Redirect GRANNY_AVAIL_DIR to a temp dir so tests never touch ~/.granny."""
    d = tmp_path / "available"
    d.mkdir()
    monkeypatch.setenv("GRANNY_AVAIL_DIR", str(d))
    # Force module re-read of the env var
    import devices.granny.availability as av

    monkeypatch.setattr(av, "_AVAILABLE_DIR", d)
    return d


class TestIsAvailable:
    def test_true_file_only_returns_true(self, avail_dir):
        from devices.granny.availability import is_available

        (avail_dir / "CC.0.available.true").touch()
        assert is_available("CC.0") is True

    def test_false_file_only_returns_false(self, avail_dir):
        from devices.granny.availability import is_available

        (avail_dir / "CC.0.available.false").touch()
        assert is_available("CC.0") is False

    def test_false_wins_over_true(self, avail_dir):
        from devices.granny.availability import is_available

        (avail_dir / "CC.0.available.true").touch()
        (avail_dir / "CC.0.available.false").touch()
        assert is_available("CC.0") is False

    def test_neither_file_returns_false(self, avail_dir):
        from devices.granny.availability import is_available

        assert is_available("CC.0") is False

    def test_granny_queue_namespace(self, avail_dir):
        from devices.granny.availability import is_available

        (avail_dir / "granny.queue.available.true").touch()
        assert is_available("granny.queue") is True

    def test_different_workers_independent(self, avail_dir):
        from devices.granny.availability import is_available

        (avail_dir / "CC.0.available.true").touch()
        (avail_dir / "DickSimnel.0.available.false").touch()
        assert is_available("CC.0") is True
        assert is_available("DickSimnel.0") is False


class TestMarkAvailable:
    def test_mark_available_creates_true_file(self, avail_dir):
        from devices.granny.availability import is_available, mark_available

        mark_available("CC.0")
        assert is_available("CC.0") is True

    def test_mark_available_removes_false_file(self, avail_dir):
        from devices.granny.availability import is_available, mark_available

        (avail_dir / "CC.0.available.false").touch()
        mark_available("CC.0")
        assert not (avail_dir / "CC.0.available.false").exists()
        assert is_available("CC.0") is True

    def test_mark_unavailable_creates_false_file(self, avail_dir):
        from devices.granny.availability import is_available, mark_unavailable

        (avail_dir / "CC.0.available.true").touch()
        mark_unavailable("CC.0")
        assert is_available("CC.0") is False

    def test_clear_removes_both_files(self, avail_dir):
        from devices.granny.availability import clear_worker_state, is_available

        (avail_dir / "CC.0.available.true").touch()
        (avail_dir / "CC.0.available.false").touch()
        clear_worker_state("CC.0")
        assert not (avail_dir / "CC.0.available.true").exists()
        assert not (avail_dir / "CC.0.available.false").exists()
        assert is_available("CC.0") is False
