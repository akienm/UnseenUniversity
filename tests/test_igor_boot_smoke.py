"""T-igor-boot-smoke-test — Igor() must instantiate end-to-end.

This smoke test exists because the Cortex constructor sweep
(T-sqlite-out-wild-0001-db, commit 9bfc31ae) missed main.py:496 — Igor()
raised TypeError on instantiation, but every other test in the suite
passed. Igor.__init__ is ~1000 lines and was uncovered: tests stub the
class (e.g. _IgorShape in test_igor_datacenter_boot.py) rather than
running the constructor.

Run as a subprocess so:
  - Igor's background threads (observer, milieu, atexit handlers) don't
    leak into the pytest process,
  - constructor-time TypeError / AttributeError surfaces as non-zero
    returncode rather than "succeeded by accident."

The conftest pg_test_schema fixture sets IGOR_HOME_SEARCH_PATH /
IGOR_LOCAL_SEARCH_PATH on os.environ; we forward those to the subprocess
so Cortex writes to the isolated test schema, not production clan.*.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

_BOOT_SNIPPET = (
    "import sys\n"
    "from wild_igor.igor.main import Igor\n"
    "igor = Igor(instance_id='boot-smoke-test')\n"
    "print('BOOT_OK', flush=True)\n"
    "sys.exit(0)\n"
)


class TestIgorBootSmoke(unittest.TestCase):
    def test_igor_instantiates_without_raising(self):
        env = {
            **os.environ,
            "IGOR_INSTANCE_ID": "boot-smoke-test",
            # Keep wire_datacenter on the no-datacenter fallback path to avoid
            # depending on a live IMAP listener.
            "AGENT_DATACENTER_TEST_MODE": "1",
        }
        result = subprocess.run(
            [sys.executable, "-c", _BOOT_SNIPPET],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            env=env,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=(
                f"Igor() boot failed (returncode={result.returncode}).\n"
                f"--- stdout ---\n{result.stdout}\n"
                f"--- stderr ---\n{result.stderr}"
            ),
        )
        self.assertIn(
            "BOOT_OK",
            result.stdout,
            msg=(
                f"Igor() did not reach the post-construct print.\n"
                f"--- stdout ---\n{result.stdout}\n"
                f"--- stderr ---\n{result.stderr}"
            ),
        )


if __name__ == "__main__":
    unittest.main()
