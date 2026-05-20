"""Tests for LegacyDirectClaimError: IGOR_STRICT_CLAIM_MODEL=1 trip-wire in cmd_claim.

# author-model: sonnet

Test plan (T-legacy-direct-claim-error):
  1. cmd_claim with IGOR_STRICT_CLAIM_MODEL=1 raises LegacyDirectClaimError
  2. cmd_claim without the flag proceeds past the env check (reaches _load)
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
    def test_raises_when_strict_flag_set(self):
        """cmd_claim raises LegacyDirectClaimError when IGOR_STRICT_CLAIM_MODEL=1."""
        with (
            patch.dict(os.environ, {"IGOR_STRICT_CLAIM_MODEL": "1"}),
            patch("lab.claudecode.cc_queue._igor_post", return_value=False),
        ):
            with pytest.raises(LegacyDirectClaimError) as exc_info:
                cmd_claim(["T-whatever"])
        assert "deprecated" in str(exc_info.value)
        assert "cmd_next" in str(exc_info.value)

    def test_posts_to_channel_when_blocked(self):
        """cmd_claim posts [CLAIM_BLOCKED] to channel before raising."""
        mock_post = MagicMock(return_value=False)
        with (
            patch.dict(os.environ, {"IGOR_STRICT_CLAIM_MODEL": "1"}),
            patch("lab.claudecode.cc_queue._igor_post", mock_post),
        ):
            with pytest.raises(LegacyDirectClaimError):
                cmd_claim(["T-whatever"])
        mock_post.assert_called_once()
        posted_content = mock_post.call_args[0][0]
        assert "CLAIM_BLOCKED" in posted_content
        assert "LegacyDirectClaimError" in posted_content

    def test_proceeds_past_check_without_flag(self):
        """cmd_claim reaches _load when IGOR_STRICT_CLAIM_MODEL is not set."""
        load_called = []

        def fake_load():
            load_called.append(True)
            return []  # empty → "Task not found" → sys.exit(1)

        env_without_flag = {
            k: v for k, v in os.environ.items() if k != "IGOR_STRICT_CLAIM_MODEL"
        }
        with (
            patch.dict(os.environ, env_without_flag, clear=True),
            patch("lab.claudecode.cc_queue._load", fake_load),
        ):
            with pytest.raises(SystemExit):
                cmd_claim(["T-nonexistent"])

        assert load_called, "cmd_claim did not reach _load — env check was not bypassed"
