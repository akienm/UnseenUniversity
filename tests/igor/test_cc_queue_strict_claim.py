"""Tests verifying that LegacyDirectClaimError exists and cmd_claim is gone.

cmd_claim was removed (T-claim-rename-dispatch). These tests are the regression guard:
if anyone tries to re-add cmd_claim, the 'not in module' test will catch it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from devlab.claudecode.cc_queue import LegacyDirectClaimError


class TestStrictClaimModel:
    def test_legacy_error_class_exists(self):
        """LegacyDirectClaimError must exist — other code may catch it."""
        assert issubclass(LegacyDirectClaimError, Exception)

    def test_cmd_claim_not_in_module(self):
        """cmd_claim must not exist on the cc_queue module."""
        import devlab.claudecode.cc_queue as q
        assert not hasattr(q, "cmd_claim"), "cmd_claim was re-added — must stay removed"

    def test_claim_not_in_commands_dict(self):
        """'claim' must not be a key in the COMMANDS dispatch dict."""
        import devlab.claudecode.cc_queue as q
        assert "claim" not in q.COMMANDS, "'claim' command was re-added — must stay removed"
