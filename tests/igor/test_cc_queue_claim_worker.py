"""Tests verifying that the 'claim' command is gone and LegacyDirectClaimError is still importable.

cmd_claim was removed (T-claim-rename-dispatch). Workers receive tickets only via Granny dispatch.
LegacyDirectClaimError stays importable so callers that catch it still compile.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import importlib.util as _ilu
_spec = _ilu.find_spec("devlab.claudecode.cc_queue")
CC_QUEUE = (
    Path(_spec.origin)
    if (_spec and _spec.origin)
    else REPO / "devlab" / "claudecode" / "cc_queue.py"
)
del _ilu, _spec

from devlab.claudecode.cc_queue import LegacyDirectClaimError


class TestClaimCommandRemoved:
    """'claim' no longer exists as a CLI command or importable function."""

    def test_legacy_direct_claim_error_is_importable(self):
        """LegacyDirectClaimError must stay importable — callers that catch it must not break."""
        assert issubclass(LegacyDirectClaimError, Exception)

    def test_cmd_claim_not_importable(self):
        """cmd_claim was removed — importing it should raise ImportError."""
        with pytest.raises(ImportError):
            from devlab.claudecode.cc_queue import cmd_claim  # noqa: F401

    def test_claim_subprocess_exits_nonzero(self):
        """cc_queue.py claim via subprocess exits non-zero (unknown command)."""
        r = subprocess.run(
            [sys.executable, str(CC_QUEUE), "claim", "T-any-ticket"],
            capture_output=True,
            text=True,
        )
        assert r.returncode != 0
