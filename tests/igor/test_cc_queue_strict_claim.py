"""Tests for LegacyDirectClaimError: cmd_claim always raises unconditionally.

# author-model: sonnet

Test plan (T-legacy-direct-claim-error):
  1. cmd_claim always raises LegacyDirectClaimError — no env flag required
  2. cmd_claim posts [CLAIM_BLOCKED] to channel before raising
  3. cmd_claim raises regardless of IGOR_STRICT_CLAIM_MODEL env value
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from lab.claudecode.cc_queue import LegacyDirectClaimError, cmd_claim


class TestStrictClaimModel:
    def test_always_raises(self):
        """cmd_claim raises LegacyDirectClaimError unconditionally — no env flag needed."""
        with patch("lab.claudecode.cc_queue._igor_post", return_value=False):
            with pytest.raises(LegacyDirectClaimError) as exc_info:
                cmd_claim(["T-whatever"])
        assert "dispatch" in str(exc_info.value)

    def test_raises_without_strict_flag(self):
        """cmd_claim raises even when IGOR_STRICT_CLAIM_MODEL is not set."""
        env_without_flag = {
            k: v for k, v in os.environ.items() if k != "IGOR_STRICT_CLAIM_MODEL"
        }
        with (
            patch.dict(os.environ, env_without_flag, clear=True),
            patch("lab.claudecode.cc_queue._igor_post", return_value=False),
        ):
            with pytest.raises(LegacyDirectClaimError):
                cmd_claim(["T-whatever"])

    def test_raises_with_strict_flag_set(self):
        """cmd_claim raises when IGOR_STRICT_CLAIM_MODEL=1 (still raises — flag is irrelevant now)."""
        with (
            patch.dict(os.environ, {"IGOR_STRICT_CLAIM_MODEL": "1"}),
            patch("lab.claudecode.cc_queue._igor_post", return_value=False),
        ):
            with pytest.raises(LegacyDirectClaimError):
                cmd_claim(["T-whatever"])

    def test_does_not_post_to_channel(self):
        """cmd_claim does NOT post to channel — prevents the NE feedback loop.

        Prior behavior (posting [CLAIM_BLOCKED] to Igor's input channel) caused
        Igor's NE to generate a response about the old model and retry, creating
        a ~20s loop. The fix: only log the attempt, never post to channel.
        """
        mock_post = MagicMock(return_value=False)
        with patch("lab.claudecode.cc_queue._igor_post", mock_post):
            with pytest.raises(LegacyDirectClaimError):
                cmd_claim(["T-whatever"])
        mock_post.assert_not_called()

    def test_logs_attempt_before_raising(self):
        """cmd_claim logs the legacy attempt before raising."""
        log_entries = []
        with (
            patch("lab.claudecode.cc_queue._log", lambda e: log_entries.append(e)),
            patch("lab.claudecode.cc_queue._igor_post", return_value=False),
        ):
            with pytest.raises(LegacyDirectClaimError):
                cmd_claim(["T-whatever"])
        assert any(
            e.get("action") == "legacy_direct_claim_attempt" for e in log_entries
        )
