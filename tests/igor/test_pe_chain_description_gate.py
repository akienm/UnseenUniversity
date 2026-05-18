"""
test_pe_chain_description_gate.py — T-pe-read-ticket-description-gate

Guard tests for the pe_read_ticket early-abort when a ticket has no
real description. Root cause of 5-week scope hallucination: SITUATE was
receiving a title-length "description" and inferring HIGH-inertia file
targets from title semantics rather than explicit Affected-files fields.

Three cases:
  1. Empty / very short description → basket["error"] set
  2. Description that is identical to the title → basket["error"] set
  3. Genuine 60+ char description → no error, chain continues
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.tools import pe_chain  # noqa: E402


class TestPeReadTicketDescriptionGate:
    def test_pe_read_ticket_aborts_on_empty_description(self):
        """Ticket with empty description → basket['error'] set, contains 'no description'."""
        fake_ticket = {
            "title": "Fix the thing",
            "description": "",
        }
        basket = {"ticket_id": "T-test-empty-desc"}
        with patch(
            "wild_igor.igor.tools.pe_chain._load_ticket",
            return_value=fake_ticket,
        ), patch("wild_igor.igor.tools.pe_chain._post_to_channel"):
            result = pe_chain.pe_read_ticket(basket)
        assert "error" in result, "Expected error key in basket"
        assert "no description" in result["error"]

    def test_pe_read_ticket_aborts_on_short_description(self):
        """Ticket with < 50 char description → basket['error'] set."""
        fake_ticket = {
            "title": "Fix the thing",
            "description": "Too short",
        }
        basket = {"ticket_id": "T-test-short-desc"}
        with patch(
            "wild_igor.igor.tools.pe_chain._load_ticket",
            return_value=fake_ticket,
        ), patch("wild_igor.igor.tools.pe_chain._post_to_channel"):
            result = pe_chain.pe_read_ticket(basket)
        assert "error" in result, "Expected error key in basket for short description"
        assert "no description" in result["error"]

    def test_pe_read_ticket_aborts_on_title_only(self):
        """Ticket where description == title → basket['error'] set."""
        title = "Refactor pe_chain scope guard to use inertia labels"
        fake_ticket = {
            "title": title,
            "description": title,  # verbatim copy — title-only escalation artifact
        }
        basket = {"ticket_id": "T-test-title-only"}
        with patch(
            "wild_igor.igor.tools.pe_chain._load_ticket",
            return_value=fake_ticket,
        ), patch("wild_igor.igor.tools.pe_chain._post_to_channel"):
            result = pe_chain.pe_read_ticket(basket)
        assert (
            "error" in result
        ), "Expected error key in basket when description == title"
        assert "no description" in result["error"]

    def test_pe_read_ticket_passes_with_real_description(self):
        """Ticket with 60+ char substantive description → no error, chain continues."""
        real_desc = (
            "Add a guard in pe_read_ticket that aborts when the description is "
            "absent or only repeats the title. Affected files: "
            "wild_igor/igor/tools/pe_chain.py. Scope: pe_read_ticket only."
        )
        assert len(real_desc) >= 60
        fake_ticket = {
            "title": "Add description gate to pe_read_ticket",
            "description": real_desc,
        }
        basket = {"ticket_id": "T-test-real-desc"}
        with patch(
            "wild_igor.igor.tools.pe_chain._load_ticket",
            return_value=fake_ticket,
        ):
            result = pe_chain.pe_read_ticket(basket)
        assert (
            "error" not in result
        ), f"Real description should not trigger gate; got error={result.get('error')!r}"
        assert result.get("ticket_description") == real_desc
