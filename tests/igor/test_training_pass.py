"""
tests/test_training_pass.py

Unit tests for D270: PROC_TRAINING_PASS — automated self-training via inner Claude.

Verifies:
- start_training_pass calls call_inner_cc_long with escalation data
- No escalations found → returns graceful early-exit message
- _read_cloud_escalations filters by tier correctly
"""

import json
import pytest
from unittest.mock import MagicMock, patch
import pathlib
import tempfile

# ── Test 1: no escalations → graceful message ─────────────────────────────────


def test_no_escalations_returns_graceful_message(tmp_path):
    from unseen_university.devices.igor.tools.training_pass import start_training_pass

    fake_log = tmp_path / "reasoning_calls.log"
    fake_log.write_text("2026-03-31|tier_select|...|selected=tier.2\n")

    with patch("unseen_university.devices.igor.tools.training_pass._REASONING_LOG", fake_log):
        result = start_training_pass()

    assert "no cloud escalations" in result.lower()


# ── Test 2: escalations found → calls inner_cc_long ──────────────────────────


def test_escalations_call_inner_cc_long(tmp_path):
    from unseen_university.devices.igor.tools.training_pass import start_training_pass

    fake_log = tmp_path / "reasoning_calls.log"
    fake_log.write_text(
        "2026-03-31T10:00:00|reasoning|openrouter|anthropic/claude-sonnet-4-6"
        "|tier=tier.4|in=100|out=20|ctx=500|cost=$0.001|elapsed=1000ms|turns=1"
        "|resp=I analyzed the cluster status.\n"
    )

    mock_result = {
        "answer": "Pattern: cluster status queries escalate to cloud.",
        "nodes": [
            {
                "type": "interpretive",
                "narrative": "Cluster status queries lack local handling.",
                "confidence": 0.8,
                "parent_cp": "CP3",
                "trigger": "cluster status",
            }
        ],
        "follow_up": "",
    }

    mock_cortex = MagicMock()

    with patch("unseen_university.devices.igor.tools.training_pass._REASONING_LOG", fake_log), patch(
        "unseen_university.devices.igor.tools.training_pass._get_cortex", return_value=mock_cortex
    ), patch(
        "unseen_university.devices.igor.tools.inner_cc.call_inner_cc_long", return_value=mock_result
    ) as mock_long:

        result = start_training_pass()

    mock_long.assert_called_once()
    assert "1 escalation" in result
    assert "1" in result  # nodes deposited


# ── Test 3: _read_cloud_escalations filters by tier ──────────────────────────


def test_read_escalations_filters_by_tier(tmp_path):
    from unseen_university.devices.igor.tools.training_pass import _read_cloud_escalations

    fake_log = tmp_path / "reasoning_calls.log"
    fake_log.write_text(
        # tier.4 — should be included
        "2026-03-31T10:00:00|reasoning|openrouter|model-a"
        "|tier=tier.4|in=100|out=20|ctx=500|cost=$0.001|elapsed=1000ms|turns=1|resp=cloud response\n"
        # tier.2 — should be excluded
        "2026-03-31T09:59:00|reasoning|ollama|llama3.2"
        "|tier=tier.2|in=80|out=15|ctx=400|cost=$0.000|elapsed=200ms|turns=1|resp=local response\n"
        # tier.3.5 — should be included (>= tier.3.5)
        "2026-03-31T09:58:00|reasoning|openrouter|model-b"
        "|tier=tier.3.5|in=90|out=18|ctx=450|cost=$0.0005|elapsed=500ms|turns=1|resp=haiku response\n"
    )

    with patch("unseen_university.devices.igor.tools.training_pass._REASONING_LOG", fake_log):
        results = _read_cloud_escalations(n=10, min_tier="tier.3.5")

    tiers = [r["tier"] for r in results]
    assert "tier.4" in tiers
    assert "tier.3.5" in tiers
    assert "tier.2" not in tiers
