"""Tests for DickSimnelShim semaphore lifecycle and dsimnel launcher wiring."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from unseen_university.devices.dicksimnel.shim import DickSimnelShim, _FLAG_DIR, _AVAILABLE_FLAG, _BLOCKED_FLAG


@pytest.fixture()
def tmp_flag_dir(tmp_path, monkeypatch):
    """Redirect semaphore flags to a temp dir so tests don't touch ~/.granny."""
    flag_dir = tmp_path / "available"
    flag_dir.mkdir()
    monkeypatch.setattr("unseen_university.devices.dicksimnel.shim._FLAG_DIR", flag_dir)
    monkeypatch.setattr("unseen_university.devices.dicksimnel.shim._AVAILABLE_FLAG", flag_dir / "DickSimnel.0.available.true")
    monkeypatch.setattr("unseen_university.devices.dicksimnel.shim._BLOCKED_FLAG", flag_dir / "DickSimnel.0.available.false")
    return flag_dir


class TestDickSimnelShimSemaphore:
    def test_start_writes_available_flag(self, tmp_flag_dir):
        shim = DickSimnelShim(worker_callback=None)
        shim.start()
        assert (tmp_flag_dir / "DickSimnel.0.available.true").exists()
        shim.stop()

    def test_stop_removes_available_flag(self, tmp_flag_dir):
        shim = DickSimnelShim(worker_callback=None)
        shim.start()
        shim.stop()
        assert not (tmp_flag_dir / "DickSimnel.0.available.true").exists()

    def test_rollback_removes_flag_if_written(self, tmp_flag_dir):
        shim = DickSimnelShim(worker_callback=None)
        shim.start()
        shim.rollback()
        assert not (tmp_flag_dir / "DickSimnel.0.available.true").exists()

    def test_rollback_noop_if_never_started(self, tmp_flag_dir):
        shim = DickSimnelShim(worker_callback=None)
        shim.rollback()  # must not raise
        assert not (tmp_flag_dir / "DickSimnel.0.available.true").exists()

    def test_is_blocked_when_false_flag_present(self, tmp_flag_dir):
        shim = DickSimnelShim(worker_callback=None)
        (tmp_flag_dir / "DickSimnel.0.available.false").write_text("false")
        assert shim.is_blocked() is True

    def test_is_not_blocked_without_false_flag(self, tmp_flag_dir):
        shim = DickSimnelShim(worker_callback=None)
        assert shim.is_blocked() is False


class TestDickSimnelGrannyIntegration:
    def test_granny_dicksimnel_available_true_when_flag_written(self, tmp_path, monkeypatch):
        """Granny's _dicksimnel_available() returns True when .true flag exists."""
        flag_dir = tmp_path / ".granny" / "available"
        flag_dir.mkdir(parents=True)
        (flag_dir / "DickSimnel.0.available.true").write_text("true")

        monkeypatch.setattr("unseen_university.devices.granny.daemon.Path", type(
            "Path", (), {"home": staticmethod(lambda: tmp_path),
                         "__truediv__": lambda s, x: tmp_path / x}
        ))
        # Simpler: patch Path.home in the module's namespace
        import pathlib
        from unseen_university.devices.granny.daemon import _dicksimnel_available
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
        assert _dicksimnel_available() is True

    def test_granny_dicksimnel_available_false_without_flag(self, tmp_path, monkeypatch):
        """Granny's _dicksimnel_available() returns False when .true flag absent."""
        flag_dir = tmp_path / ".granny" / "available"
        flag_dir.mkdir(parents=True)
        # No .true flag written

        import pathlib
        from unseen_university.devices.granny.daemon import _dicksimnel_available
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path))
        assert _dicksimnel_available() is False
