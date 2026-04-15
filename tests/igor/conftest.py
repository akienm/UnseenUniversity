"""conftest.py — pytest session fixtures for TheIgors tests.

Prevents tests from creating directories in the live ~/.TheIgors/ instance.
Uses property patching rather than env vars so subprocesses are unaffected.
"""

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _redirect_inbox_to_test_dir(tmp_path_factory):
    """Redirect paths().inbox to a temp dir for the whole test session.

    Without this, tests that call FileInboxChannel().acquire() trigger
    paths().inbox.mkdir(parents=True, exist_ok=True), which creates
    ~/.TheIgors/Igor-wild-0001/inbox/ (the default instance when
    IGOR_INSTANCE_ID is not set in the test environment).
    """
    test_inbox = tmp_path_factory.mktemp("igor_test_inbox")

    from wild_igor.igor.paths import PathManager

    orig_inbox = PathManager.inbox.fget

    PathManager.inbox = property(lambda self: test_inbox)

    yield test_inbox

    PathManager.inbox = property(orig_inbox)


@pytest.fixture(autouse=True, scope="session")
def _test_data_lifecycle():
    """T-test-data-lifecycle: auto-tag + auto-cleanup throwaway test data.

    Sets IGOR_TEST_MODE=1 so cortex.store() stamps metadata.test_data=True
    and metadata.test_expires_at on every memory created during the test
    session. On session teardown, deletes all rows matching the tag.

    The tag-based cleanup is the primary mechanism; the TTL is a
    belt-and-suspenders safeguard for crashed/interrupted test runs.
    Opt-out: tests that want cross-session persistence can set
    metadata.test_data=False explicitly.
    """
    prior_flag = os.environ.get("IGOR_TEST_MODE")
    os.environ["IGOR_TEST_MODE"] = "1"

    yield

    # Restore env var
    if prior_flag is None:
        os.environ.pop("IGOR_TEST_MODE", None)
    else:
        os.environ["IGOR_TEST_MODE"] = prior_flag

    # Best-effort cleanup — never block the test session on failure
    try:
        from wild_igor.igor.memory.cortex import Cortex
        from wild_igor.igor.memory.test_data_lifecycle import cleanup_test_data
        from wild_igor.igor.paths import paths as _paths

        cortex = Cortex(db_path=str(_paths().instance / "wild-0001.db"))
        removed = cleanup_test_data(cortex)
        if removed:
            print(f"\n[test_data_lifecycle] cleaned up {removed} test memories")
    except Exception as exc:
        print(f"\n[test_data_lifecycle] cleanup skipped: {exc}")
