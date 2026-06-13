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
