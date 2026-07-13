"""
Shared pytest configuration for the tests/ directory.

Sets AGENT_DATACENTER_TEST_MODE=1 before any test module is imported so that
bus.imap_server._TEST_MODE evaluates to True in every test that touches the
bus. This avoids the ordering hazard where a test file that doesn't set the
var caches the module in production mode before stub-reliant tests run.
"""

import os
import sys
import sqlite3 as _real_sqlite3

import pytest

os.environ.setdefault("AGENT_DATACENTER_TEST_MODE", "1")


@pytest.fixture(autouse=True)
def _restore_sqlite3():
    """Restore real sqlite3 before each test.

    devices.igor._sqlite_guard replaces sys.modules["sqlite3"] with a guard
    that raises RuntimeError on connect(). It installs during collection
    (module-level imports in test_device_contract.py etc.) and leaks into
    tests outside devices/igor/ that legitimately use sqlite3. Restoring
    before each test body runs ensures no cross-test contamination.
    """
    sys.modules["sqlite3"] = _real_sqlite3
    yield


@pytest.fixture(autouse=True)
def _redirect_log_root(tmp_path, monkeypatch):
    """Redirect default per-device log output to a tmp dir (T-per-device-log-hierarchy).

    DiagnosticBase devices that don't pin their own _log_root default to the
    canonical ~/.unseen_university/logs. Without this, instantiating any such device
    in a test would write JSON log files into the user's real home. UU_LOG_ROOT is
    the hermetic-redirect knob (mirrors UU_MEMORY_ROOT). Tests that need the real
    default path delenv it explicitly.
    """
    monkeypatch.setenv("UU_LOG_ROOT", str(tmp_path / "uu_logs"))
    yield


@pytest.fixture(autouse=True)
def _preserve_igor_home_db_url():
    """Preserve UU_HOME_DB_URL across tests.

    Some tests pop UU_HOME_DB_URL for isolation. This fixture guarantees
    restoration after every test so order-dependent failures don't cascade.
    """
    saved = os.environ.get("UU_HOME_DB_URL")
    yield
    if saved is not None:
        os.environ["UU_HOME_DB_URL"] = saved
    elif "UU_HOME_DB_URL" in os.environ:
        del os.environ["UU_HOME_DB_URL"]


# ── live tests carry a HARD wall-clock ceiling ────────────────────────────────
#
# T-default-suite-drives-live-inference-and-saturates-hex. Deselecting `live` by default stops the
# *unnoticed* live run, which is what let nine of them pile up. This is the second belt: even a run
# you asked for (`-m live`) cannot become an orphan. On 2026-07-13 the oldest such orphan had been
# alive 1h57m, still holding a socket to Hex and still adding load to the queue that everyone else —
# including my own diagnosis of the problem — was measuring.
#
# An orphaned process is not like a stale file. It is invisible AND IT KEEPS ACTING.
LIVE_TEST_CEILING_SECONDS = 600


@pytest.fixture(autouse=True)
def _live_tests_cannot_outlive_a_ceiling(request):
    """Kill any @pytest.mark.live test that overruns. No dependency, no daemon — just SIGALRM."""
    if request.node.get_closest_marker("live") is None:
        yield
        return

    import signal

    def _blow_up(_signum, _frame):
        raise TimeoutError(
            f"live test exceeded its {LIVE_TEST_CEILING_SECONDS}s ceiling and was killed. A live "
            f"test that runs forever does not fail — it ORPHANS, holding its connection and adding "
            f"load to the shared host it is measuring. That is how nine of them accumulated."
        )

    prev = signal.signal(signal.SIGALRM, _blow_up)
    signal.alarm(LIVE_TEST_CEILING_SECONDS)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, prev)
