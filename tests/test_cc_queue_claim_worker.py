"""Tests for cc_queue.py cmd_claim — removed, always raises LegacyDirectClaimError.

# author-model: opus

Original: tested the cert_worker_freeze design — worker routing through cmd_claim.

Updated 2026-05-20: cmd_claim is removed. Workers must use:
    cc_queue.py next --worker <name>
All four original claim-routing cases are obsolete. These tests now verify that
any invocation of cmd_claim raises LegacyDirectClaimError unconditionally,
regardless of worker, flags, or DB state.
"""

from __future__ import annotations

import importlib.util as _ilu
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

_spec = _ilu.find_spec("lab.claudecode.cc_queue")
CC_QUEUE = (
    Path(_spec.origin)
    if (_spec and _spec.origin)
    else REPO / "lab" / "claudecode" / "cc_queue.py"
)
del _ilu, _spec

from lab.claudecode.cc_queue import LegacyDirectClaimError, cmd_claim


def _run_claim(*args: str) -> subprocess.CompletedProcess:
    """Invoke cc_queue.py claim via subprocess."""
    return subprocess.run(
        [sys.executable, str(CC_QUEUE), "claim", *args],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )


class TestCmdClaimRemoved:
    """cmd_claim always raises LegacyDirectClaimError — all routing logic is gone."""

    def test_claim_always_raises_in_process(self):
        """cmd_claim raises LegacyDirectClaimError in-process, no env flag required."""
        with patch("lab.claudecode.cc_queue._igor_post", return_value=False):
            with pytest.raises(LegacyDirectClaimError) as exc_info:
                cmd_claim(["T-any-ticket"])
        assert "next --worker" in str(exc_info.value)

    def test_claim_subprocess_exits_nonzero(self):
        """cc_queue.py claim via subprocess exits non-zero and prints the error."""
        r = _run_claim("T-any-ticket")
        assert r.returncode != 0
        assert "LegacyDirectClaimError" in r.stderr or "no longer supported" in r.stderr

    def test_claim_subprocess_with_as_flag_also_exits_nonzero(self):
        """--as flag does not restore old behavior — claim is gone."""
        r = _run_claim("T-any-ticket", "--as", "claude")
        assert r.returncode != 0

    def test_claim_subprocess_with_igor_worker_also_exits_nonzero(self):
        """worker=igor path is also gone — no special case."""
        r = _run_claim("T-any-ticket", "--as", "igor")
        assert r.returncode != 0
