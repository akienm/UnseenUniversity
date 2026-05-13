"""tests/test_cc_inbox_producer.py — Igor-side CC inbox producer hooks.

Covers:
- cc_inbox_bridge.post_to_cc_inbox() is non-fatal on import failure
- cc_inbox_bridge.post_to_cc_inbox() forwards args to underlying append()
- pe_chain HIGH-inertia DESIGN_PROPOSAL path posts to CC inbox with
  urgency=high, response_expected=True, correct kind + ticket_id
- pe_chain block path posts with urgency=normal, response_expected=True
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# ── cc_inbox_bridge.post_to_cc_inbox ────────────────────────────────────────


class TestPostToCcInboxBridge:
    def test_forwards_args_to_append(self):
        from wild_igor.igor.cognition import cc_inbox_bridge

        with patch("lab.claudecode.cc_inbox.append") as mock_append:
            cc_inbox_bridge.post_to_cc_inbox(
                kind="test_kind",
                summary="test summary",
                body="test body",
                ticket_id="T-test",
                urgency="high",
                response_expected=True,
            )

        mock_append.assert_called_once()
        _, kwargs = mock_append.call_args
        assert kwargs["kind"] == "test_kind"
        assert kwargs["summary"] == "test summary"
        assert kwargs["body"] == "test body"
        assert kwargs["ticket_id"] == "T-test"
        assert kwargs["urgency"] == "high"
        assert kwargs["response_expected"] is True

    def test_non_fatal_on_append_exception(self):
        from wild_igor.igor.cognition import cc_inbox_bridge

        with patch("lab.claudecode.cc_inbox.append", side_effect=RuntimeError("boom")):
            # Must not raise
            cc_inbox_bridge.post_to_cc_inbox(kind="k", summary="s")

    def test_default_urgency_normal(self):
        from wild_igor.igor.cognition import cc_inbox_bridge

        with patch("lab.claudecode.cc_inbox.append") as mock_append:
            cc_inbox_bridge.post_to_cc_inbox(kind="k", summary="s")

        _, kwargs = mock_append.call_args
        assert kwargs["urgency"] == "normal"
        assert kwargs["response_expected"] is False


# ── pe_chain HIGH-inertia DESIGN_PROPOSAL → inbox ───────────────────────────


class TestPeChainDesignProposalPostsToInbox:
    def test_high_inertia_proposal_calls_cc_inbox(self, tmp_path, monkeypatch):
        """When _pe_escalate hits the HIGH-inertia branch with a real file,
        it must post a DESIGN_PROPOSAL entry to CC inbox with urgency=high
        and response_expected=True."""
        from wild_igor.igor.tools import pe_chain

        # Use a real HIGH-inertia file so the hallucinated-file rewrite doesn't
        # trigger (we want the HIGH-inertia branch, not the block branch)
        real_high_inertia = "wild_igor/igor/brainstem/core_patterns.py"
        basket = {
            "ticket_id": "T-demo-proposal",
            "hypothesis": {
                "file": real_high_inertia,
                "old_string": "a",
                "new_string": "b",
            },
            "plan_summary": "refactor X",
            "op_type": "write",
            # Description must mention the file so the cross-check passes.
            "ticket_description": (
                f"Refactor the genesis pattern loader.\n"
                f"Affected files: {real_high_inertia}"
            ),
        }

        with patch(
            "wild_igor.igor.cognition.cc_inbox_bridge.post_to_cc_inbox"
        ) as mock_cc_post, patch.object(pe_chain, "_post_to_channel"), patch.object(
            pe_chain, "_run_bash", return_value={"stdout": "", "stderr": "", "rc": 0}
        ):
            pe_chain.PeChain(basket=basket)._pe_escalate(
                reason="HIGH inertia write requires human approval: "
                + real_high_inertia,
            )

        assert mock_cc_post.called, "expected cc_inbox post on HIGH-inertia proposal"
        _, kwargs = mock_cc_post.call_args
        assert kwargs["kind"] == "pe_chain_design_proposal"
        assert kwargs["urgency"] == "high"
        assert kwargs["response_expected"] is True
        assert kwargs["ticket_id"] == "T-demo-proposal"


# ── pe_chain block path → inbox ──────────────────────────────────────────────


class TestPeChainBlockPostsToInbox:
    def test_block_path_calls_cc_inbox(self):
        """When _pe_escalate hits the block branch (non-HIGH-inertia reason),
        CC inbox gets a pe_chain_block entry."""
        from wild_igor.igor.tools import pe_chain

        basket = {
            "ticket_id": "T-demo-block",
            "attempt_count": 3,
            "hypothesis": {"file": "wild_igor/igor/tools/some_tool.py"},
        }

        with patch(
            "wild_igor.igor.cognition.cc_inbox_bridge.post_to_cc_inbox"
        ) as mock_cc_post, patch.object(pe_chain, "_post_to_channel"), patch.object(
            pe_chain, "_run_bash", return_value={"stdout": "", "stderr": "", "rc": 0}
        ):
            pe_chain.PeChain(basket=basket)._pe_escalate(
                reason="test-suite broken (unrelated)"
            )

        assert mock_cc_post.called
        _, kwargs = mock_cc_post.call_args
        assert kwargs["kind"] == "pe_chain_block"
        assert kwargs["urgency"] == "normal"
        assert kwargs["response_expected"] is True
        assert kwargs["ticket_id"] == "T-demo-block"
        assert "T-demo-block" in kwargs["summary"]

    def test_block_path_non_fatal_if_cc_post_raises(self):
        """Producer-site failure must not break pe_chain escalation path."""
        from wild_igor.igor.tools import pe_chain

        basket = {
            "ticket_id": "T-fail-demo",
            "attempt_count": 1,
            "hypothesis": {"file": "wild_igor/igor/tools/x.py"},
        }

        with patch(
            "wild_igor.igor.cognition.cc_inbox_bridge.post_to_cc_inbox",
            side_effect=RuntimeError("inbox down"),
        ), patch.object(pe_chain, "_post_to_channel"), patch.object(
            pe_chain, "_run_bash", return_value={"stdout": "", "stderr": "", "rc": 0}
        ):
            # Must complete without raising despite inbox failure
            result = pe_chain.PeChain(basket=basket)._pe_escalate(
                reason="test-suite unrelated"
            )

        assert result.get("escalate_reason") == "test-suite unrelated"
