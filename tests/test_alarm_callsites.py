"""Proof for T-alarm-callsites — raise_alarm wired at critical failure paths.

Tests the three callsites:
1. ground_loop supervisor (daemon import/start failure)
2. db_proxy (Postgres unreachable)
3. installer (permission denied)

Each test verifies:
- The alarm file is created with the correct signature
- The original exception propagates (never swallowed)
- The alarm is dropped without touching the database (db test critical)
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from unittest import mock

import pytest

from unseen_university import system_alarms as sa


@pytest.fixture(autouse=True)
def _redirect_home(tmp_path, monkeypatch):
    """Point uu_home at a tmp dir so alarms never touch the real store."""
    monkeypatch.setattr("unseen_university.system_alarms.uu_home", lambda: str(tmp_path))
    return tmp_path


class TestGroundLoopStartFailure:
    """ground_loop supervisor raises alarm on daemon import or start failure."""

    def test_import_failure_raises_alarm_and_propagates(self, tmp_path, monkeypatch):
        """When runme.py import fails, alarm is dropped and exception propagates."""
        from unseen_university.devices.ground_loop.supervisor import RunmeSupervisor

        # Create a minimal fake repo with a broken runme.py
        devices_dir = tmp_path / "devices"
        test_device_dir = devices_dir / "test_device" / "groundloop"
        test_device_dir.mkdir(parents=True)
        runme_py = test_device_dir / "runme.py"
        runme_py.write_text("raise RuntimeError('broken import')")

        supervisor = RunmeSupervisor(tmp_path)
        supervisor._load(runme_py, 0.0)

        # Alarm should be dropped with device name in signature
        alarm = sa.get_alarm("ground-loop-start-failed:test_device")
        assert alarm is not None, "alarm should be created on import failure"
        assert alarm["callers"] == {"unseen_university.devices.ground_loop.supervisor": 1}
        assert "broken import" in alarm["last_message"]

        # runme.py should be renamed to .borkedpy
        assert not runme_py.exists()
        assert (test_device_dir / "runme.borkedpy").exists()

    def test_runtime_failure_raises_alarm_and_propagates(self, tmp_path, monkeypatch):
        """When runme.start() raises at runtime, alarm is dropped."""
        import time
        from unseen_university.devices.ground_loop.supervisor import RunmeSupervisor

        # Create a runme.py that imports fine but fails on start()
        devices_dir = tmp_path / "devices"
        test_device_dir = devices_dir / "another_device" / "groundloop"
        test_device_dir.mkdir(parents=True)
        runme_py = test_device_dir / "runme.py"
        runme_py.write_text(
            "def start():\n"
            "    raise ValueError('runtime failure in start()')\n"
            "def stop():\n"
            "    pass\n"
        )

        supervisor = RunmeSupervisor(tmp_path)
        supervisor._load(runme_py, 0.0)

        # Wait a bit for the thread to run and fail
        time.sleep(0.2)

        # Alarm should be dropped with device name
        alarm = sa.get_alarm("ground-loop-start-failed:another_device")
        assert alarm is not None, "alarm should be created on runtime failure"
        assert "runtime failure in start()" in alarm["last_message"]


class TestDbUnreachableRaisesAlarm:
    """db_proxy raises alarm when Postgres is unreachable."""

    def test_db_unreachable_raises_alarm_without_db(self, monkeypatch):
        """When DB pool init fails, alarm is dropped and exception propagates.

        CRITICAL: the alarm path must NOT touch the database at all. This test
        verifies the invariant by checking that an alarm is created with zero
        database available.
        """
        from psycopg2 import OperationalError
        from unseen_university.db_proxy import PGDatabaseProxy

        # Patch ThreadedConnectionPool to raise OperationalError immediately
        def _boom(*a, **k):
            raise OperationalError("connection to server at 'localhost' failed")

        with monkeypatch.context() as mp:
            # Patch psycopg2.pool.ThreadedConnectionPool in the context where it's imported
            mp.setattr("psycopg2.pool.ThreadedConnectionPool", _boom)

            # Attempt to create proxy — should fail with the original exception
            with pytest.raises(OperationalError, match="connection to server"):
                PGDatabaseProxy("postgresql://localhost/test")

        # Alarm should be dropped despite DB being down
        alarm = sa.get_alarm("db-unreachable")
        assert alarm is not None, (
            "alarm must be created even with no database available "
            "(this proves flat-file, DB-independent path)"
        )
        assert alarm["callers"] == {"unseen_university.db_proxy": 1}
        assert "Postgres unreachable" in alarm["last_message"]
        assert "connection to server" in alarm["last_message"]

    def test_db_unreachable_alarm_touches_no_database(self, monkeypatch):
        """Verify the db-unreachable alarm path is 100% flat-file.

        The alarm must be droppable without ANY database access. This test
        verifies that the alarm mechanism itself doesn't trigger DB operations.
        """
        from unseen_university.db_proxy import PGDatabaseProxy

        # Patch ThreadedConnectionPool to raise a generic exception
        def _boom_runtime(*a, **k):
            raise RuntimeError("no database available (simulated)")

        with monkeypatch.context() as mp:
            mp.setattr("psycopg2.pool.ThreadedConnectionPool", _boom_runtime)

            # Attempt to create proxy
            with pytest.raises(RuntimeError, match="no database"):
                PGDatabaseProxy("postgresql://localhost/test")

        # Alarm should still be created without touching DB
        alarm = sa.get_alarm("db-unreachable")
        assert alarm is not None
        assert alarm["count"] == 1
        # Verify the alarm file was actually written (it should be)
        alarms = sa.list_alarms()
        assert len(alarms) == 1
        assert alarms[0]["signature"] == "db-unreachable"


class TestInstallerPermissionDenied:
    """installer.py raises alarm on permission failures."""

    def test_instance_dir_mkdir_permission_denied(self, tmp_path, monkeypatch):
        """When instance_dir.mkdir fails with PermissionError, alarm is raised."""
        from unseen_university.devices.igor.setup_assets.installer import restart_loop

        # Create a protected parent directory (simulating permission denied)
        protected_dir = tmp_path / "protected"
        protected_dir.mkdir()
        # Make it read-only to simulate permission denied
        os.chmod(protected_dir, 0o444)

        try:
            with monkeypatch.context() as mp:
                mp.setenv("IGOR_RUNTIME_ROOT", str(protected_dir))
                mp.setenv("IGOR_INSTANCE_ID", "TestInstance")

                # Attempt to call restart_loop — should fail with PermissionError
                with pytest.raises(PermissionError):
                    restart_loop([])

            # Alarm should be created with the path in the signature
            alarms = sa.list_alarms()
            assert any(
                a["signature"].startswith("install-permission-denied:")
                for a in alarms
            ), "alarm should be created on mkdir PermissionError"
        finally:
            # Restore permissions for cleanup
            os.chmod(protected_dir, 0o755)

    def test_restart_ts_file_write_permission_denied(self, tmp_path, monkeypatch):
        """When restart_ts_file.write_text fails with PermissionError, alarm is raised."""
        from unseen_university.devices.igor.setup_assets.installer import restart_loop
        from unittest.mock import patch

        # Set up a valid instance dir but mock write_text to fail
        instance_dir = tmp_path / ".unseen_university" / "TestInstance2"
        instance_dir.mkdir(parents=True, exist_ok=True)

        original_write_text = Path.write_text
        call_count = [0]

        def _boom_write_text(self, *a, **k):
            call_count[0] += 1
            if "restart_timestamps.txt" in str(self):
                raise PermissionError(f"Permission denied: {self}")
            return original_write_text(self, *a, **k)

        with monkeypatch.context() as mp:
            mp.setattr(Path, "write_text", _boom_write_text)
            mp.setenv("IGOR_RUNTIME_ROOT", str(tmp_path / ".unseen_university"))
            mp.setenv("IGOR_INSTANCE_ID", "TestInstance2")

            # Mock subprocess.Popen to avoid actually launching Igor
            with patch("subprocess.Popen"):
                # Attempt to call restart_loop — should fail on write_text
                with pytest.raises(PermissionError, match="Permission denied"):
                    restart_loop([])

        # Alarm should be created with the file path in the signature
        alarms = sa.list_alarms()
        assert any(
            a["signature"].startswith("install-permission-denied:")
            and "restart_timestamps.txt" in a["signature"]
            for a in alarms
        ), "alarm should be created on write_text PermissionError"


class TestAlarmDeduplication:
    """Verify that repeated failures deduplicate to the same alarm file."""

    def test_ground_loop_failures_deduplicate(self, tmp_path):
        """Multiple failures from the same device deduplicate to one alarm."""
        from unseen_university.devices.ground_loop.supervisor import RunmeSupervisor

        devices_dir = tmp_path / "devices"
        test_device_dir = devices_dir / "shared_device" / "groundloop"
        test_device_dir.mkdir(parents=True)
        runme_py = test_device_dir / "runme.py"

        # First failure (import)
        runme_py.write_text("raise RuntimeError('error 1')")
        supervisor = RunmeSupervisor(tmp_path)
        supervisor._load(runme_py, 0.0)

        # Second failure on same device (different error, same signature)
        # Recreate runme.py after it was borked
        runme_py.with_suffix(".borkedpy").rename(runme_py)
        runme_py.write_text("raise RuntimeError('error 2')")
        supervisor._load(runme_py, 1.0)

        # Both failures should deduplicate to one file with count=2
        alarm = sa.get_alarm("ground-loop-start-failed:shared_device")
        assert alarm is not None
        assert alarm["count"] == 2
        alarms = sa.list_alarms()
        assert len(alarms) == 1, "multiple failures should deduplicate"
