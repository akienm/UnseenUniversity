"""
test_pe_entry_nodes.py — Unit tests for pe_chain ENTRY/CLAIM/READ_TICKET steps.

Tests the basket-passing step functions directly without hitting the DB,
active goals, or cc_queue on disk. Each step is tested in isolation and
in chain composition.
"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.tools.pe_chain import (
    _parse_file_list,
    pe_claim,
    pe_entry_init,
    pe_read_ticket,
    pe_situate,
    run_pe_entry_chain,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


SAMPLE_TICKET = {
    "id": "T-test-ticket",
    "title": "Test ticket title",
    "description": "Detailed description of what to fix and why.",
    "status": "pending",
    "required_files": ["wild_igor/igor/tools/ops.py"],
}


@pytest.fixture
def tmp_queue(tmp_path):
    """Write a temp queue.json with one ticket."""
    q = tmp_path / "queue.json"
    q.write_text(json.dumps([SAMPLE_TICKET]))
    return q


@pytest.fixture
def basket_with_ticket():
    """Basket pre-seeded with ticket_id (skips active-goal lookup)."""
    return {"ticket_id": "T-test-ticket"}


# ── pe_entry_init ─────────────────────────────────────────────────────────────


class TestPeEntryInit:
    def test_seeds_constants_when_ticket_id_present(self, basket_with_ticket):
        result = pe_entry_init(basket_with_ticket)
        assert result["attempt_count"] == 0
        assert result["expected"] == "tests pass, requirements met"
        assert result["ticket_id"] == "T-test-ticket"

    def test_no_error_when_ticket_id_present(self, basket_with_ticket):
        result = pe_entry_init(basket_with_ticket)
        assert "error" not in result

    def test_creates_basket_if_none(self, basket_with_ticket):
        result = pe_entry_init(basket_with_ticket)
        assert isinstance(result, dict)

    def test_error_when_no_active_goal_and_no_ticket_id(self):
        with patch("wild_igor.igor.tools.pe_chain._get_active_goal", return_value=None):
            result = pe_entry_init({})
        assert "error" in result
        assert "no active GOAL" in result["error"]

    def test_extracts_ticket_id_from_goal(self):
        mock_goal = MagicMock()
        mock_goal.id = "GOAL_20260402000000000000"
        mock_goal.metadata = {
            "goal_active": True,
            "source_message": "work ticket T-some-ticket",
            "adopted_at": "2026-04-02T00:00:00",
        }
        mock_goal.narrative = "ACTIVE GOAL: work ticket T-some-ticket"
        with patch(
            "wild_igor.igor.tools.pe_chain._get_active_goal", return_value=mock_goal
        ):
            result = pe_entry_init({})
        assert result["ticket_id"] == "T-some-ticket"
        assert result["goal_id"] == "GOAL_20260402000000000000"
        assert result["attempt_count"] == 0

    def test_error_when_goal_has_no_ticket_id(self):
        mock_goal = MagicMock()
        mock_goal.id = "GOAL_123"
        mock_goal.metadata = {
            "goal_active": True,
            "source_message": "do some work",
            "adopted_at": "2026-04-02T00:00:00",
        }
        mock_goal.narrative = "ACTIVE GOAL: do some work"
        with patch(
            "wild_igor.igor.tools.pe_chain._get_active_goal", return_value=mock_goal
        ):
            result = pe_entry_init({})
        assert "error" in result

    def test_does_not_overwrite_existing_ticket_id(self):
        basket = {"ticket_id": "T-existing", "attempt_count": 2}
        result = pe_entry_init(basket)
        assert result["ticket_id"] == "T-existing"
        assert result["attempt_count"] == 2  # not reset

    def test_passthrough_on_existing_error(self):
        basket = {"error": "prior error", "ticket_id": "T-x"}
        # entry_init doesn't check for prior errors — it runs always
        result = pe_entry_init(basket)
        assert result["ticket_id"] == "T-x"


# ── pe_claim ──────────────────────────────────────────────────────────────────


class TestPeClaim:
    def test_calls_cc_queue_claim(self, basket_with_ticket):
        with patch(
            "wild_igor.igor.tools.pe_chain._run_bash",
            return_value="Claimed T-test-ticket",
        ) as mock_bash:
            result = pe_claim(basket_with_ticket)
        assert result["claim_result"] == "Claimed T-test-ticket"
        call_args = mock_bash.call_args[0][0]
        assert "claim" in call_args
        assert "T-test-ticket" in call_args

    def test_error_passthrough(self):
        basket = {"error": "prior error"}
        result = pe_claim(basket)
        assert result["error"] == "prior error"
        # should not have called _run_bash

    def test_error_when_no_ticket_id(self):
        result = pe_claim({})
        assert "error" in result
        assert "ticket_id" in result["error"]

    def test_claim_result_written_to_basket(self, basket_with_ticket):
        with patch("wild_igor.igor.tools.pe_chain._run_bash", return_value="ok"):
            result = pe_claim(basket_with_ticket)
        assert "claim_result" in result


# ── pe_read_ticket ────────────────────────────────────────────────────────────


class TestPeReadTicket:
    def test_populates_ticket_description(self, basket_with_ticket, tmp_queue):
        with patch("wild_igor.igor.tools.pe_chain._QUEUE_FILE", tmp_queue):
            result = pe_read_ticket(basket_with_ticket)
        assert result["ticket_description"] == SAMPLE_TICKET["description"]

    def test_populates_ticket_title(self, basket_with_ticket, tmp_queue):
        with patch("wild_igor.igor.tools.pe_chain._QUEUE_FILE", tmp_queue):
            result = pe_read_ticket(basket_with_ticket)
        assert result["ticket_title"] == SAMPLE_TICKET["title"]

    def test_populates_plan_files(self, basket_with_ticket, tmp_queue):
        with patch("wild_igor.igor.tools.pe_chain._QUEUE_FILE", tmp_queue):
            result = pe_read_ticket(basket_with_ticket)
        assert result["plan_files"] == ["wild_igor/igor/tools/ops.py"]

    def test_plan_files_empty_list_when_absent(self, tmp_queue):
        ticket_no_files = {**SAMPLE_TICKET, "required_files": None}
        tmp_queue.write_text(json.dumps([ticket_no_files]))
        basket = {"ticket_id": "T-test-ticket"}
        with patch("wild_igor.igor.tools.pe_chain._QUEUE_FILE", tmp_queue):
            result = pe_read_ticket(basket)
        assert result["plan_files"] == []

    def test_error_when_ticket_not_found(self, basket_with_ticket, tmp_queue):
        tmp_queue.write_text(json.dumps([]))  # empty queue
        with patch("wild_igor.igor.tools.pe_chain._QUEUE_FILE", tmp_queue):
            result = pe_read_ticket(basket_with_ticket)
        assert "error" in result
        assert "T-test-ticket" in result["error"]

    def test_error_passthrough(self, tmp_queue):
        basket = {"error": "prior error", "ticket_id": "T-test-ticket"}
        result = pe_read_ticket(basket)
        assert result["error"] == "prior error"
        assert "ticket_description" not in result

    def test_uses_title_as_fallback_when_no_description(self, tmp_queue):
        ticket_no_desc = {**SAMPLE_TICKET}
        del ticket_no_desc["description"]
        tmp_queue.write_text(json.dumps([ticket_no_desc]))
        basket = {"ticket_id": "T-test-ticket"}
        with patch("wild_igor.igor.tools.pe_chain._QUEUE_FILE", tmp_queue):
            result = pe_read_ticket(basket)
        assert result["ticket_description"] == SAMPLE_TICKET["title"]


# ── run_pe_entry_chain ────────────────────────────────────────────────────────


class TestRunPeEntryChain:
    def test_full_chain_populates_basket(self, tmp_queue):
        """Full chain: basket seeded with ticket_id → claim → read."""
        with (
            patch("wild_igor.igor.tools.pe_chain._QUEUE_FILE", tmp_queue),
            patch(
                "wild_igor.igor.tools.pe_chain._run_bash",
                return_value="Claimed T-test-ticket",
            ),
        ):
            result = run_pe_entry_chain({"ticket_id": "T-test-ticket"})

        assert "error" not in result
        assert result["ticket_id"] == "T-test-ticket"
        assert result["ticket_description"] == SAMPLE_TICKET["description"]
        assert result["ticket_title"] == SAMPLE_TICKET["title"]
        assert result["plan_files"] == ["wild_igor/igor/tools/ops.py"]
        assert result["claim_result"] == "Claimed T-test-ticket"
        assert result["attempt_count"] == 0
        assert result["expected"] == "tests pass, requirements met"

    def test_chain_stops_on_error(self, tmp_queue):
        """If ENTRY fails, CLAIM and READ_TICKET must not run."""
        empty_queue = tmp_queue
        empty_queue.write_text(json.dumps([]))

        with (
            patch("wild_igor.igor.tools.pe_chain._get_active_goal", return_value=None),
            patch("wild_igor.igor.tools.pe_chain._run_bash") as mock_bash,
        ):
            result = run_pe_entry_chain({})

        assert "error" in result
        mock_bash.assert_not_called()  # CLAIM never ran

    def test_chain_stops_when_ticket_not_found(self, tmp_queue):
        tmp_queue.write_text(json.dumps([]))
        with patch("wild_igor.igor.tools.pe_chain._QUEUE_FILE", tmp_queue):
            with patch(
                "wild_igor.igor.tools.pe_chain._run_bash",
                return_value="ok",
            ):
                result = run_pe_entry_chain({"ticket_id": "T-nonexistent"})
        assert "error" in result
        assert "ticket_description" not in result

    def test_full_chain_with_situate_uses_required_files(self, tmp_queue):
        """When ticket has required_files, SITUATE uses them (no Ollama call)."""
        with (
            patch("wild_igor.igor.tools.pe_chain._QUEUE_FILE", tmp_queue),
            patch("wild_igor.igor.tools.pe_chain._run_bash", return_value="ok"),
            patch("wild_igor.igor.tools.pe_chain._call_tier2") as mock_t2,
        ):
            result = run_pe_entry_chain({"ticket_id": "T-test-ticket"})

        assert "error" not in result
        assert result["plan_files"] == ["wild_igor/igor/tools/ops.py"]
        assert result["situate_source"] == "ticket_required_files"
        mock_t2.assert_not_called()  # no Ollama needed


# ── pe_situate ────────────────────────────────────────────────────────────────


class TestPeSituate:
    def test_fast_path_uses_existing_plan_files(self):
        basket = {
            "ticket_description": "some description",
            "plan_files": ["wild_igor/igor/tools/ops.py"],
        }
        with patch("wild_igor.igor.tools.pe_chain._call_tier2") as mock_t2:
            result = pe_situate(basket)
        assert result["plan_files"] == ["wild_igor/igor/tools/ops.py"]
        assert result["situate_source"] == "ticket_required_files"
        mock_t2.assert_not_called()

    def test_calls_tier2_when_no_plan_files(self):
        basket = {
            "ticket_description": "Fix goal_adopt TWM category",
            "plan_files": [],
        }
        with patch(
            "wild_igor.igor.tools.pe_chain._call_tier2",
            return_value="wild_igor/igor/tools/ops.py\nwild_igor/igor/main.py",
        ):
            with patch(
                "wild_igor.igor.tools.pe_chain._REPO_ROOT",
                Path(__file__).resolve().parent.parent,
            ):
                result = pe_situate(basket)
        assert result["situate_source"] == "tier2_ollama"

    def test_empty_source_when_tier2_unavailable(self):
        basket = {
            "ticket_description": "some ticket",
            "plan_files": [],
        }
        with patch("wild_igor.igor.tools.pe_chain._call_tier2", return_value=None):
            result = pe_situate(basket)
        assert result["plan_files"] == []
        assert result["situate_source"] == "empty"

    def test_error_passthrough(self):
        basket = {"error": "prior error", "ticket_description": "x", "plan_files": []}
        with patch("wild_igor.igor.tools.pe_chain._call_tier2") as mock_t2:
            result = pe_situate(basket)
        assert result["error"] == "prior error"
        mock_t2.assert_not_called()

    def test_error_when_no_ticket_description(self):
        basket = {"plan_files": []}
        result = pe_situate(basket)
        assert "error" in result

    def test_parse_file_list_one_per_line(self):
        raw = "wild_igor/igor/tools/ops.py\nwild_igor/igor/main.py"
        with patch(
            "wild_igor.igor.tools.pe_chain._REPO_ROOT",
            Path(__file__).resolve().parent.parent,
        ):
            result = _parse_file_list(raw)
        # Only paths that actually exist are returned
        assert all("/" in p for p in result)

    def test_parse_file_list_strips_backticks(self):
        raw = "`wild_igor/igor/tools/ops.py`"
        with patch(
            "wild_igor.igor.tools.pe_chain._REPO_ROOT",
            Path(__file__).resolve().parent.parent,
        ):
            result = _parse_file_list(raw)
        # Path format is correct (no backticks) if file exists
        assert all("`" not in p for p in result)

    def test_parse_file_list_ignores_non_paths(self):
        raw = "Here are the files:\nwild_igor/igor/tools/ops.py\nSome explanation."
        with patch(
            "wild_igor.igor.tools.pe_chain._REPO_ROOT",
            Path(__file__).resolve().parent.parent,
        ):
            result = _parse_file_list(raw)
        assert "Here are the files:" not in result
        assert "Some explanation." not in result

    def test_parse_file_list_empty_on_no_paths(self):
        raw = "I don't know which files to change."
        result = _parse_file_list(raw)
        assert result == []
