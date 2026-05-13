"""
test_pe_chain_single_ticket_mode.py — T-igor-single-ticket-mode

# author-model: opus

Tests the IGOR_SINGLE_TICKET env-var kill-switch in pe_chain. When set to a
ticket id, only that ticket may pass ENTRY; all others fail with an error
message naming the allowed id.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.tools import pe_chain  # noqa: E402


class TestEnforceSingleTicketMode:
    def test_unset_env_var_is_passthrough(self, monkeypatch):
        monkeypatch.delenv("IGOR_SINGLE_TICKET", raising=False)
        basket = {"ticket_id": "T-anything"}
        result = pe_chain.PeChain(basket=basket)._enforce_single_ticket_mode()
        assert "error" not in result
        assert result["ticket_id"] == "T-anything"

    def test_empty_env_var_is_passthrough(self, monkeypatch):
        monkeypatch.setenv("IGOR_SINGLE_TICKET", "")
        basket = {"ticket_id": "T-anything"}
        result = pe_chain.PeChain(basket=basket)._enforce_single_ticket_mode()
        assert "error" not in result

    def test_whitespace_only_env_var_is_passthrough(self, monkeypatch):
        monkeypatch.setenv("IGOR_SINGLE_TICKET", "   ")
        basket = {"ticket_id": "T-anything"}
        result = pe_chain.PeChain(basket=basket)._enforce_single_ticket_mode()
        assert "error" not in result

    def test_matching_ticket_passes(self, monkeypatch):
        monkeypatch.setenv("IGOR_SINGLE_TICKET", "T-cc-walk-02")
        basket = {"ticket_id": "T-cc-walk-02"}
        result = pe_chain.PeChain(basket=basket)._enforce_single_ticket_mode()
        assert "error" not in result
        assert result["ticket_id"] == "T-cc-walk-02"

    def test_nonmatching_ticket_blocked(self, monkeypatch):
        monkeypatch.setenv("IGOR_SINGLE_TICKET", "T-cc-walk-02")
        basket = {"ticket_id": "T-other"}
        result = pe_chain.PeChain(basket=basket)._enforce_single_ticket_mode()
        assert "error" in result
        assert "single_ticket_mode" in result["error"]
        assert "T-cc-walk-02" in result["error"]
        assert "T-other" in result["error"]

    def test_no_ticket_id_blocked_when_env_set(self, monkeypatch):
        """If env var is set but the basket has no ticket_id, block — the
        whole point is that nothing autonomous slips through."""
        monkeypatch.setenv("IGOR_SINGLE_TICKET", "T-cc-walk-02")
        basket: dict = {}
        result = pe_chain.PeChain(basket=basket)._enforce_single_ticket_mode()
        assert "error" in result

    def test_env_strips_leading_trailing_whitespace(self, monkeypatch):
        monkeypatch.setenv("IGOR_SINGLE_TICKET", "  T-cc-walk-02  ")
        basket = {"ticket_id": "T-cc-walk-02"}
        result = pe_chain.PeChain(basket=basket)._enforce_single_ticket_mode()
        assert "error" not in result


class TestPeEntryInitIntegration:
    """pe_entry_init applies the gate after seeding ticket_id."""

    def test_existing_ticket_id_blocked_when_mode_disagrees(self, monkeypatch):
        monkeypatch.setenv("IGOR_SINGLE_TICKET", "T-cc-walk-02")
        basket = {"ticket_id": "T-other"}
        result = pe_chain.pe_entry_init(basket)
        assert "error" in result
        assert "single_ticket_mode" in result["error"]

    def test_existing_ticket_id_passes_when_mode_agrees(self, monkeypatch):
        monkeypatch.setenv("IGOR_SINGLE_TICKET", "T-cc-walk-02")
        basket = {"ticket_id": "T-cc-walk-02"}
        result = pe_chain.pe_entry_init(basket)
        assert "error" not in result
        assert result["ticket_id"] == "T-cc-walk-02"

    def test_mode_unset_preserves_normal_behavior(self, monkeypatch):
        """Regression: unset env var → existing ENTRY behavior is unchanged."""
        monkeypatch.delenv("IGOR_SINGLE_TICKET", raising=False)
        basket = {"ticket_id": "T-anything"}
        result = pe_chain.pe_entry_init(basket)
        assert "error" not in result
        assert result["ticket_id"] == "T-anything"
        assert result["attempt_count"] == 0
