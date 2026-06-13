"""T-machine-manager-lazy-db-url-check — guard fires at first connect, not import.

Before this fix, importing machine_manager without UU_HOME_DB_URL set
raised RuntimeError at module-load time, breaking test discovery and
static analysis. The guard now runs inside _pg_connect so the error lands
exactly when someone touches the DB.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys

import pytest


def test_import_succeeds_with_env_unset():
    """Importing machine_manager must not raise when UU_HOME_DB_URL is empty.

    Uses a subprocess so we can clear the env var without contaminating the
    test process. A fresh interpreter + clean env validates the true
    import-time behavior.
    """
    env = {k: v for k, v in os.environ.items() if k != "UU_HOME_DB_URL"}
    env["UU_HOME_DB_URL"] = ""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import devices.igor.tools.machine_manager; print('ok')",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, f"import failed: {result.stderr}"
    assert "ok" in result.stdout


def test_db_url_helper_raises_with_clear_message(monkeypatch):
    """The connect-time guard still emits the exact error message the ops
    docs rely on."""
    from devices.igor.tools import machine_manager as mm

    monkeypatch.setenv("UU_HOME_DB_URL", "")
    with pytest.raises(RuntimeError) as excinfo:
        mm._db_url()
    assert "UU_HOME_DB_URL not set" in str(excinfo.value)


def test_db_url_returns_value_when_set(monkeypatch):
    from devices.igor.tools import machine_manager as mm

    monkeypatch.setenv("UU_HOME_DB_URL", "postgresql://test")
    assert mm._db_url() == "postgresql://test"


def test_pg_connect_surfaces_runtime_error_at_first_use(monkeypatch):
    """Concrete regression: import succeeded (tested above); now calling
    _pg_connect with the env unset should raise the same RuntimeError."""
    from devices.igor.tools import machine_manager as mm

    monkeypatch.setenv("UU_HOME_DB_URL", "")
    with pytest.raises(RuntimeError) as excinfo:
        mm._pg_connect()
    assert "UU_HOME_DB_URL not set" in str(excinfo.value)
