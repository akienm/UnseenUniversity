"""conftest.py — pytest session fixtures for TheIgors tests.

Prevents tests from creating directories in the live ~/.TheIgors/ instance.
Uses property patching rather than env vars so subprocesses are unaffected.
"""

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
