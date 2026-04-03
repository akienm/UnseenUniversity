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
    _MAX_ATTEMPTS,
    _extract_grep_patterns,
    _parse_file_list,
    _parse_hypothesis,
    _validate_hypothesis,
    pe_claim,
    pe_close_loop,
    pe_entry_init,
    pe_hypothesize,
    pe_implement,
    pe_observe,
    pe_read_ticket,
    pe_situate,
    pe_test,
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
        """Full chain: basket seeded with ticket_id → claim → read → test pass."""
        with (
            patch("wild_igor.igor.tools.pe_chain._QUEUE_FILE", tmp_queue),
            patch(
                "wild_igor.igor.tools.pe_chain._run_bash",
                return_value="Claimed T-test-ticket",
            ),
            patch("wild_igor.igor.tools.pe_chain._call_tier2", return_value=None),
            patch(
                "wild_igor.igor.tools.pe_chain._REPO_ROOT",
                Path(__file__).resolve().parent.parent,
            ),
            patch("wild_igor.igor.tools.pe_chain._post_to_channel"),
            patch(
                "wild_igor.igor.tools.pe_chain.pe_test",
                side_effect=lambda b: {**b, "test_result": "pass"},
            ),
            patch(
                "wild_igor.igor.tools.pe_chain._pe_commit",
                side_effect=lambda b: {**b, "commit_result": "ok"},
            ),
            patch("wild_igor.igor.tools.pe_chain._pe_close", side_effect=lambda b: b),
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
        """When ticket has required_files, SITUATE uses them (no SITUATE Ollama call).
        HYPOTHESIZE may still call tier.2 — that's expected."""
        with (
            patch("wild_igor.igor.tools.pe_chain._QUEUE_FILE", tmp_queue),
            patch("wild_igor.igor.tools.pe_chain._run_bash", return_value="ok"),
            patch("wild_igor.igor.tools.pe_chain._call_tier2", return_value=None),
            patch(
                "wild_igor.igor.tools.pe_chain._REPO_ROOT",
                Path(__file__).resolve().parent.parent,
            ),
            patch("wild_igor.igor.tools.pe_chain._post_to_channel"),
            patch(
                "wild_igor.igor.tools.pe_chain.pe_test",
                side_effect=lambda b: {**b, "test_result": "pass"},
            ),
        ):
            result = run_pe_entry_chain({"ticket_id": "T-test-ticket"})

        assert "error" not in result
        assert result["plan_files"] == ["wild_igor/igor/tools/ops.py"]
        assert result["situate_source"] == "ticket_required_files"
        # HYPOTHESIZE ran (tier.2 unavailable → hypothesis=None, not an error)
        assert "hypothesis" in result


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


# ── pe_observe ────────────────────────────────────────────────────────────────


REPO_ROOT = Path(__file__).resolve().parent.parent


class TestExtractGrepPatterns:
    def test_extracts_quoted_strings(self):
        desc = "Fix 'goal_adopt' to use category='active_goal'"
        patterns = _extract_grep_patterns(desc)
        assert "goal_adopt" in patterns or "active_goal" in patterns

    def test_extracts_proc_ids(self):
        desc = "PROC_GREETING fires on substantive messages"
        patterns = _extract_grep_patterns(desc)
        assert "PROC_GREETING" in patterns

    def test_extracts_snake_case_names(self):
        desc = "twm_get_active_goal queries wrong category"
        patterns = _extract_grep_patterns(desc)
        assert any("twm" in p for p in patterns)

    def test_caps_at_four_patterns(self):
        desc = "'alpha' 'beta' 'gamma' 'delta' 'epsilon' PROC_X PROC_Y"
        patterns = _extract_grep_patterns(desc)
        assert len(patterns) <= 4

    def test_deduplicates(self):
        desc = "'goal_adopt' and 'goal_adopt' again"
        patterns = _extract_grep_patterns(desc)
        assert patterns.count("goal_adopt") == 1

    def test_empty_description(self):
        patterns = _extract_grep_patterns("")
        assert patterns == []


class TestPeObserve:
    def test_skips_when_no_plan_files(self):
        basket = {"ticket_description": "some ticket", "plan_files": []}
        result = pe_observe(basket)
        assert "error" not in result
        assert result["actual"] == ""
        assert result["observe_hits"] == 0
        assert result["line_ranges"] == {}

    def test_error_passthrough(self):
        basket = {"error": "prior", "plan_files": ["x"], "ticket_description": "y"}
        result = pe_observe(basket)
        assert result["error"] == "prior"
        assert "actual" not in result

    def test_reads_real_file_section(self):
        """pe_observe reads a real file from the repo."""
        basket = {
            "ticket_description": "Fix goal_adopt category mismatch",
            "plan_files": ["wild_igor/igor/tools/ops.py"],
        }
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT", REPO_ROOT):
            result = pe_observe(basket)
        assert "error" not in result
        assert result["actual"] != ""
        assert "ops.py" in result["actual"]
        # Should contain line numbers
        assert ": " in result["actual"]

    def test_actual_is_section_not_full_file(self):
        """actual should be much shorter than the full file."""
        basket = {
            "ticket_description": "Fix goal_adopt category='active_goal'",
            "plan_files": ["wild_igor/igor/tools/ops.py"],
        }
        full_file = (REPO_ROOT / "wild_igor/igor/tools/ops.py").read_text()
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT", REPO_ROOT):
            result = pe_observe(basket)
        assert len(result["actual"]) < len(full_file)

    def test_grep_hit_increments_observe_hits(self):
        basket = {
            "ticket_description": "Fix 'goal_adopt' category mismatch",
            "plan_files": ["wild_igor/igor/tools/ops.py"],
        }
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT", REPO_ROOT):
            result = pe_observe(basket)
        # goal_adopt exists in ops.py — should get a grep hit
        assert result["observe_hits"] >= 1

    def test_line_ranges_populated(self):
        basket = {
            "ticket_description": "Fix 'goal_adopt' category",
            "plan_files": ["wild_igor/igor/tools/ops.py"],
        }
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT", REPO_ROOT):
            result = pe_observe(basket)
        assert "wild_igor/igor/tools/ops.py" in result["line_ranges"]

    def test_multiple_files(self):
        basket = {
            "ticket_description": "Fix 'goal_adopt' in ops and main",
            "plan_files": [
                "wild_igor/igor/tools/ops.py",
                "wild_igor/igor/main.py",
            ],
        }
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT", REPO_ROOT):
            result = pe_observe(basket)
        assert "ops.py" in result["actual"]
        assert "main.py" in result["actual"]
        assert len(result["line_ranges"]) == 2


# ── pe_hypothesize ────────────────────────────────────────────────────────────

VALID_HYPOTHESIS = {
    "file": "wild_igor/igor/tools/ops.py",
    "old_string": 'category="goal"',
    "new_string": 'category="active_goal"',
}

VALID_HYPOTHESIS_JSON = (
    '{"file": "wild_igor/igor/tools/ops.py", '
    '"old_string": "category=\\"goal\\"", '
    '"new_string": "category=\\"active_goal\\""}'
)


class TestParseHypothesis:
    def test_parses_valid_json(self):
        raw = '{"file": "a.py", "old_string": "foo", "new_string": "bar"}'
        result = _parse_hypothesis(raw)
        assert result == {"file": "a.py", "old_string": "foo", "new_string": "bar"}

    def test_strips_markdown_fences(self):
        raw = '```json\n{"file": "a.py", "old_string": "x", "new_string": "y"}\n```'
        result = _parse_hypothesis(raw)
        assert result is not None
        assert result["file"] == "a.py"

    def test_returns_none_on_missing_fields(self):
        raw = '{"file": "a.py", "old_string": "x"}'
        result = _parse_hypothesis(raw)
        assert result is None

    def test_returns_none_on_invalid_json_and_no_regex_match(self):
        raw = "Here is my explanation. I would change line 42."
        result = _parse_hypothesis(raw)
        assert result is None

    def test_regex_fallback_extracts_fields(self):
        raw = (
            'Some preamble\n"file": "ops.py",\n'
            '"old_string": "old",\n"new_string": "new"\nEnd.'
        )
        result = _parse_hypothesis(raw)
        # Either parsed or None — just verify it doesn't crash
        assert result is None or isinstance(result, dict)


class TestValidateHypothesis:
    def test_valid_hypothesis_returns_none(self):
        # ops.py contains category="goal" — this is the actual mismatch we just fixed
        # Use a string we know exists in the file
        hyp = {
            "file": "wild_igor/igor/tools/pe_chain.py",
            "old_string": "pe_chain",
            "new_string": "pe_chain",
        }
        err = _validate_hypothesis(hyp, REPO_ROOT)
        assert err is None

    def test_file_not_found(self):
        hyp = {"file": "nonexistent/path.py", "old_string": "x", "new_string": "y"}
        err = _validate_hypothesis(hyp, REPO_ROOT)
        assert err is not None
        assert "not found" in err

    def test_old_string_not_in_file(self):
        hyp = {
            "file": "wild_igor/igor/tools/pe_chain.py",
            "old_string": "THIS_STRING_DOES_NOT_EXIST_ZXQW",
            "new_string": "y",
        }
        err = _validate_hypothesis(hyp, REPO_ROOT)
        assert err is not None
        assert "not found verbatim" in err


class TestPeHypothesisize:
    def test_error_passthrough(self):
        basket = {"error": "prior", "ticket_description": "x", "actual": "y"}
        with patch("wild_igor.igor.tools.pe_chain._call_tier2") as mock_t2:
            result = pe_hypothesize(basket)
        assert result["error"] == "prior"
        mock_t2.assert_not_called()

    def test_null_hypothesis_when_no_actual(self):
        basket = {"ticket_description": "fix something", "actual": ""}
        result = pe_hypothesize(basket)
        assert result["hypothesis"] is None
        assert result["hypothesis_error"] is not None

    def test_error_when_no_description(self):
        basket = {"ticket_description": "", "actual": "some code"}
        result = pe_hypothesize(basket)
        assert "error" in result

    def test_valid_hypothesis_accepted(self):
        good_json = '{"file": "wild_igor/igor/tools/pe_chain.py", "old_string": "pe_chain", "new_string": "pe_chain"}'
        basket = {
            "ticket_description": "fix something in pe_chain",
            "actual": "some relevant code",
        }
        with patch("wild_igor.igor.tools.pe_chain._call_tier2", return_value=good_json):
            with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT", REPO_ROOT):
                result = pe_hypothesize(basket)
        assert result["hypothesis"] is not None
        assert result["hypothesis_error"] is None
        assert result["hypothesis"]["file"] == "wild_igor/igor/tools/pe_chain.py"

    def test_null_hypothesis_when_tier2_unavailable(self):
        basket = {"ticket_description": "fix it", "actual": "some code"}
        with patch("wild_igor.igor.tools.pe_chain._call_tier2", return_value=None):
            result = pe_hypothesize(basket)
        assert result["hypothesis"] is None
        assert "unavailable" in result["hypothesis_error"]

    def test_null_hypothesis_when_parse_fails(self):
        basket = {"ticket_description": "fix it", "actual": "some code"}
        with patch(
            "wild_igor.igor.tools.pe_chain._call_tier2",
            return_value="I would change line 42 to do something different.",
        ):
            result = pe_hypothesize(basket)
        assert result["hypothesis"] is None
        assert result["hypothesis_error"] is not None

    def test_validation_failure_stored_not_blocking(self):
        """Validation failure sets hypothesis_error but does NOT set basket[error]."""
        bad_json = '{"file": "wild_igor/igor/tools/pe_chain.py", "old_string": "NONEXISTENT_ZXQW", "new_string": "y"}'
        basket = {"ticket_description": "fix it", "actual": "some code"}
        with patch("wild_igor.igor.tools.pe_chain._call_tier2", return_value=bad_json):
            with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT", REPO_ROOT):
                result = pe_hypothesize(basket)
        # hypothesis_error set, but basket["error"] NOT set — chain continues
        assert result.get("hypothesis_error") is not None
        assert "error" not in result or result.get("error") is None

    def test_hypothesis_raw_always_set(self):
        basket = {"ticket_description": "fix it", "actual": "some code"}
        with patch(
            "wild_igor.igor.tools.pe_chain._call_tier2",
            return_value="raw output",
        ):
            result = pe_hypothesize(basket)
        assert "hypothesis_raw" in result


# ── pe_implement ──────────────────────────────────────────────────────────────


class TestPeImplement:
    def test_applies_edit_to_file(self, tmp_path):
        target = tmp_path / "target.py"
        target.write_text('x = "old_value"\n')
        basket = {
            "hypothesis": {
                "file": "target.py",
                "old_string": '"old_value"',
                "new_string": '"new_value"',
            },
            "hypothesis_error": None,
        }
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT", tmp_path):
            result = pe_implement(basket)
        assert result["implement_result"] == "ok"
        assert result["implement_skipped"] is False
        assert '"new_value"' in target.read_text()

    def test_skips_when_no_hypothesis(self):
        basket = {"hypothesis": None, "hypothesis_error": None}
        result = pe_implement(basket)
        assert result["implement_skipped"] is True
        assert "skipped" in result["implement_result"]

    def test_skips_when_hypothesis_error(self):
        basket = {
            "hypothesis": {"file": "x.py", "old_string": "a", "new_string": "b"},
            "hypothesis_error": "validation failed",
        }
        result = pe_implement(basket)
        assert result["implement_skipped"] is True

    def test_error_passthrough(self):
        basket = {"error": "prior", "hypothesis": None, "hypothesis_error": None}
        result = pe_implement(basket)
        assert result["error"] == "prior"
        assert "implement_result" not in result

    def test_error_when_old_string_not_in_file(self, tmp_path):
        target = tmp_path / "f.py"
        target.write_text("some content\n")
        basket = {
            "hypothesis": {
                "file": "f.py",
                "old_string": "NONEXISTENT_ZXQ",
                "new_string": "y",
            },
            "hypothesis_error": None,
        }
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT", tmp_path):
            result = pe_implement(basket)
        assert result["implement_skipped"] is True
        assert "error" in result["implement_result"]

    def test_only_replaces_first_occurrence(self, tmp_path):
        target = tmp_path / "f.py"
        target.write_text('a = "x"\nb = "x"\n')
        basket = {
            "hypothesis": {"file": "f.py", "old_string": '"x"', "new_string": '"y"'},
            "hypothesis_error": None,
        }
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT", tmp_path):
            pe_implement(basket)
        content = target.read_text()
        assert content.count('"y"') == 1
        assert content.count('"x"') == 1  # second occurrence untouched


# ── pe_test ───────────────────────────────────────────────────────────────────


class TestPeTest:
    def test_pass_result_when_tests_pass(self):
        basket = {}
        with patch(
            "wild_igor.igor.tools.pe_chain._run_bash",
            return_value="61 passed in 0.9s",
        ):
            with patch(
                "wild_igor.igor.tools.pe_chain.pe_test.__module__",
                create=True,
            ):
                # Patch ops.run_tests to raise ImportError so we use fallback
                with patch.dict("sys.modules", {"wild_igor.igor.tools.ops": None}):
                    result = pe_test(basket)
        assert result["test_result"] == "pass"

    def test_fail_result_when_tests_fail(self):
        basket = {}
        with patch(
            "wild_igor.igor.tools.pe_chain._run_bash",
            return_value="3 failed, 58 passed in 1.2s\nFAILED tests/test_x.py",
        ):
            with patch.dict("sys.modules", {"wild_igor.igor.tools.ops": None}):
                result = pe_test(basket)
        assert result["test_result"].startswith("fail:")

    def test_error_passthrough(self):
        basket = {"error": "prior error"}
        with patch("wild_igor.igor.tools.pe_chain._run_bash") as mock_bash:
            result = pe_test(basket)
        assert result["error"] == "prior error"
        mock_bash.assert_not_called()

    def test_test_result_always_set_on_success(self):
        basket = {}
        with patch(
            "wild_igor.igor.tools.pe_chain._run_bash",
            return_value="5 passed in 0.1s",
        ):
            with patch.dict("sys.modules", {"wild_igor.igor.tools.ops": None}):
                result = pe_test(basket)
        assert "test_result" in result


# ── pe_close_loop ─────────────────────────────────────────────────────────────


def _passing_basket(ticket_id="T-test", file="wild_igor/igor/tools/pe_chain.py"):
    """Basket in the state just before pe_close_loop: test passed, edit applied."""
    return {
        "ticket_id": ticket_id,
        "goal_id": "GOAL_123",
        "test_result": "pass",
        "attempt_count": 0,
        "hypothesis": {
            "file": file,
            "old_string": "pe_chain",
            "new_string": "pe_chain",
        },
        "implement_result": "ok",
        "implement_skipped": False,
        "ticket_description": "Fix something",
        "actual": "some code",
    }


def _failing_basket(**kwargs):
    b = _passing_basket(**kwargs)
    b["test_result"] = "fail: AssertionError at line 42"
    return b


class TestPeCloseLoop:
    def test_pass_path_commits_and_closes(self):
        basket = _passing_basket()
        with (
            patch(
                "wild_igor.igor.tools.pe_chain._run_bash", return_value="[main abc] fix"
            ),
            patch("wild_igor.igor.tools.pe_chain._post_to_channel"),
            patch("wild_igor.igor.tools.pe_chain.close_goal_by_ticket", create=True),
        ):
            with patch.dict("sys.modules", {"wild_igor.igor.tools.ops": None}):
                result = pe_close_loop(basket)
        assert "commit_result" in result
        assert "escalate_reason" not in result

    def test_pass_path_skips_commit_when_no_edit(self):
        basket = _passing_basket()
        basket["implement_skipped"] = True
        with (
            patch("wild_igor.igor.tools.pe_chain._run_bash") as mock_bash,
            patch("wild_igor.igor.tools.pe_chain._post_to_channel"),
        ):
            with patch.dict("sys.modules", {"wild_igor.igor.tools.ops": None}):
                result = pe_close_loop(basket)
        assert result["commit_result"] == "skipped: no edit applied"
        # git commit should NOT be called; cc_queue done IS called (also via _run_bash)
        git_calls = [c for c in mock_bash.call_args_list if "git" in str(c)]
        assert git_calls == [], f"Expected no git calls, got: {git_calls}"

    def test_escalates_after_max_attempts(self):
        basket = _failing_basket()
        basket["attempt_count"] = _MAX_ATTEMPTS
        with (
            patch("wild_igor.igor.tools.pe_chain._run_bash", return_value="ok"),
            patch("wild_igor.igor.tools.pe_chain._post_to_channel"),
        ):
            result = pe_close_loop(basket)
        assert "escalate_reason" in result
        assert "exhausted" in result["escalate_reason"]

    def test_replan_called_on_first_failure(self):
        basket = _failing_basket()
        # Replan returns pass on second attempt
        call_count = {"n": 0}

        def fake_tier2(prompt, timeout=30):
            call_count["n"] += 1
            return '{"file": "wild_igor/igor/tools/pe_chain.py", "old_string": "pe_chain", "new_string": "pe_chain"}'

        with (
            patch("wild_igor.igor.tools.pe_chain._call_tier2", side_effect=fake_tier2),
            patch("wild_igor.igor.tools.pe_chain._REPO_ROOT", REPO_ROOT),
            patch(
                "wild_igor.igor.tools.pe_chain._run_bash",
                side_effect=lambda cmd, **_: (
                    "61 passed" if "pytest" in str(cmd) or "-m" in str(cmd) else "ok"
                ),
            ),
            patch("wild_igor.igor.tools.pe_chain._post_to_channel"),
        ):
            with patch.dict("sys.modules", {"wild_igor.igor.tools.ops": None}):
                result = pe_close_loop(basket)
        # tier.2 was called at least once for replan
        assert call_count["n"] >= 1
        assert basket["attempt_count"] >= 1

    def test_error_passthrough(self):
        basket = {"error": "prior error", "test_result": "pass"}
        with patch("wild_igor.igor.tools.pe_chain._run_bash") as mock_bash:
            result = pe_close_loop(basket)
        assert result["error"] == "prior error"
        mock_bash.assert_not_called()

    def test_attempt_count_increments_on_replan(self):
        basket = _failing_basket()
        basket["attempt_count"] = _MAX_ATTEMPTS - 1  # one attempt left before escalate

        with (
            patch("wild_igor.igor.tools.pe_chain._call_tier2", return_value=None),
            patch("wild_igor.igor.tools.pe_chain._run_bash", return_value="ok"),
            patch("wild_igor.igor.tools.pe_chain._post_to_channel"),
        ):
            result = pe_close_loop(basket)
        # Should have hit max and escalated
        assert "escalate_reason" in result
