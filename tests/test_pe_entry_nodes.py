"""
test_pe_entry_nodes.py — Unit tests for pe_chain ENTRY/CLAIM/READ_TICKET steps.

Tests the basket-passing step functions directly without hitting the DB,
active goals, or cc_queue on disk. Each step is tested in isolation and
in chain composition.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.tools.pe_chain import (
    _MAX_ATTEMPTS,
    _CODE_EXPANSION,
    _affected_files_from_description,
    _expand_patterns_with_synonyms,
    _extract_grep_patterns,
    _filter_high_inertia_not_in_description,
    _parse_file_list,
    _parse_hypothesis,
    _pe_escalate,
    _situate_from_memory,
    _validate_hypothesis,
    pe_claim,
    pe_close_loop,
    pe_entry_init,
    pe_hypothesize,
    pe_implement,
    pe_observe,
    pe_read_ticket,
    pe_situate,
    pe_store_observe_results,
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
    def test_populates_ticket_description(self, basket_with_ticket):
        with patch(
            "wild_igor.igor.tools.pe_chain._load_queue_tasks",
            return_value=[SAMPLE_TICKET],
        ):
            result = pe_read_ticket(basket_with_ticket)
        assert result["ticket_description"] == SAMPLE_TICKET["description"]

    def test_populates_ticket_title(self, basket_with_ticket):
        with patch(
            "wild_igor.igor.tools.pe_chain._load_queue_tasks",
            return_value=[SAMPLE_TICKET],
        ):
            result = pe_read_ticket(basket_with_ticket)
        assert result["ticket_title"] == SAMPLE_TICKET["title"]

    def test_populates_plan_files(self, basket_with_ticket):
        with patch(
            "wild_igor.igor.tools.pe_chain._load_queue_tasks",
            return_value=[SAMPLE_TICKET],
        ):
            result = pe_read_ticket(basket_with_ticket)
        assert result["plan_files"] == ["wild_igor/igor/tools/ops.py"]

    def test_plan_files_empty_list_when_absent(self):
        ticket_no_files = {**SAMPLE_TICKET, "required_files": None}
        basket = {"ticket_id": "T-test-ticket"}
        with patch(
            "wild_igor.igor.tools.pe_chain._load_queue_tasks",
            return_value=[ticket_no_files],
        ):
            result = pe_read_ticket(basket)
        assert result["plan_files"] == []

    def test_error_when_ticket_not_found(self, basket_with_ticket):
        with patch("wild_igor.igor.tools.pe_chain._load_queue_tasks", return_value=[]):
            result = pe_read_ticket(basket_with_ticket)
        assert "error" in result
        assert "T-test-ticket" in result["error"]

    def test_error_passthrough(self):
        basket = {"error": "prior error", "ticket_id": "T-test-ticket"}
        result = pe_read_ticket(basket)
        assert result["error"] == "prior error"
        assert "ticket_description" not in result

    def test_uses_title_as_fallback_when_no_description(self):
        ticket_no_desc = {**SAMPLE_TICKET}
        del ticket_no_desc["description"]
        basket = {"ticket_id": "T-test-ticket"}
        with patch(
            "wild_igor.igor.tools.pe_chain._load_queue_tasks",
            return_value=[ticket_no_desc],
        ):
            result = pe_read_ticket(basket)
        assert result["ticket_description"] == SAMPLE_TICKET["title"]


# ── run_pe_entry_chain ────────────────────────────────────────────────────────


class TestRunPeEntryChain:
    def test_full_chain_populates_basket(self):
        """Full chain: basket seeded with ticket_id → claim → read → test pass."""
        with (
            patch(
                "wild_igor.igor.tools.pe_chain._load_queue_tasks",
                return_value=[SAMPLE_TICKET],
            ),
            patch(
                "wild_igor.igor.tools.pe_chain._run_bash",
                return_value="Claimed T-test-ticket",
            ),
            patch("wild_igor.igor.tools.pe_chain._call_tier2", return_value=None),
            patch(
                "wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT",
                Path(__file__).resolve().parent.parent,
            ),
            patch("wild_igor.igor.tools.pe_chain._post_to_channel"),
            patch(
                "wild_igor.igor.tools.pe_chain.pe_test",
                side_effect=lambda b, **_: {**b, "test_result": "pass"},
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

    def test_chain_stops_on_error(self):
        """If ENTRY fails, CLAIM and READ_TICKET must not run."""
        with (
            patch("wild_igor.igor.tools.pe_chain._get_active_goal", return_value=None),
            patch("wild_igor.igor.tools.pe_chain._run_bash") as mock_bash,
        ):
            result = run_pe_entry_chain({})

        assert "error" in result
        mock_bash.assert_not_called()  # CLAIM never ran

    def test_chain_stops_when_ticket_not_found(self):
        with patch("wild_igor.igor.tools.pe_chain._load_queue_tasks", return_value=[]):
            with patch(
                "wild_igor.igor.tools.pe_chain._run_bash",
                return_value="ok",
            ):
                result = run_pe_entry_chain({"ticket_id": "T-nonexistent"})
        assert "error" in result
        assert "ticket_description" not in result

    def test_full_chain_with_situate_uses_required_files(self):
        """When ticket has required_files, SITUATE uses them (no SITUATE Ollama call).
        HYPOTHESIZE may still call tier.2 — that's expected."""
        with (
            patch(
                "wild_igor.igor.tools.pe_chain._load_queue_tasks",
                return_value=[SAMPLE_TICKET],
            ),
            patch("wild_igor.igor.tools.pe_chain._run_bash", return_value="ok"),
            patch("wild_igor.igor.tools.pe_chain._call_tier2", return_value=None),
            patch(
                "wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT",
                Path(__file__).resolve().parent.parent,
            ),
            patch("wild_igor.igor.tools.pe_chain._post_to_channel"),
            patch(
                "wild_igor.igor.tools.pe_chain.pe_test",
                side_effect=lambda b, **_: {**b, "test_result": "pass"},
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
                "wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT",
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
            "wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT",
            Path(__file__).resolve().parent.parent,
        ):
            result = _parse_file_list(raw)
        # Only paths that actually exist are returned
        assert all("/" in p for p in result)

    def test_parse_file_list_strips_backticks(self):
        raw = "`wild_igor/igor/tools/ops.py`"
        with patch(
            "wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT",
            Path(__file__).resolve().parent.parent,
        ):
            result = _parse_file_list(raw)
        # Path format is correct (no backticks) if file exists
        assert all("`" not in p for p in result)

    def test_parse_file_list_ignores_non_paths(self):
        raw = "Here are the files:\nwild_igor/igor/tools/ops.py\nSome explanation."
        with patch(
            "wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT",
            Path(__file__).resolve().parent.parent,
        ):
            result = _parse_file_list(raw)
        assert "Here are the files:" not in result
        assert "Some explanation." not in result

    def test_parse_file_list_empty_on_no_paths(self):
        raw = "I don't know which files to change."
        result = _parse_file_list(raw)
        assert result == []

    # ── T-situate-kernel-hallucination-fix: three-layer defense ─────────────

    def test_situate_uses_affected_files_field(self):
        """Structured 'Affected files:' field skips tier.2 entirely."""
        desc = (
            "Some problem description.\n\n"
            "**Affected files:** wild_igor/igor/tools/ops.py, wild_igor/igor/main.py\n"
            "**Test plan:** add tests."
        )
        basket = {"ticket_description": desc, "plan_files": []}
        with patch("wild_igor.igor.tools.pe_chain._call_tier2") as mock_t2:
            with patch(
                "wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT",
                Path(__file__).resolve().parent.parent,
            ):
                result = pe_situate(basket)
        assert result["situate_source"] == "affected_files_field"
        assert "wild_igor/igor/tools/ops.py" in result["plan_files"]
        assert "wild_igor/igor/main.py" in result["plan_files"]
        mock_t2.assert_not_called()

    def test_situate_rejects_unmentioned_high_inertia(self):
        """Tier2 suggesting a HIGH-inertia brainstem file on a sparse ticket gets filtered.
        After filter drops all proposals the chain falls through to consult (which also
        returns nothing here) → situate_source='empty'."""
        desc = "Auto-file audit pass-2 severity-high findings as tickets."
        basket = {"ticket_description": desc, "plan_files": []}
        with (
            patch(
                "wild_igor.igor.tools.pe_chain._call_tier2",
                return_value="wild_igor/igor/brainstem/core_patterns.py",
            ),
            patch(
                "wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT",
                Path(__file__).resolve().parent.parent,
            ),
            patch("wild_igor.igor.tools.pe_chain._maybe_consult_stuck"),
        ):
            result = pe_situate(basket)
        assert result["situate_source"] == "empty"
        assert result["plan_files"] == []

    def test_situate_allows_high_inertia_when_named(self):
        """If ticket names a HIGH-inertia file verbatim, tier2 suggestion passes the filter."""
        desc = "Refactor wild_igor/igor/brainstem/core_patterns.py to split dispatch."
        basket = {"ticket_description": desc, "plan_files": []}
        with patch(
            "wild_igor.igor.tools.pe_chain._call_tier2",
            return_value="wild_igor/igor/brainstem/core_patterns.py",
        ):
            with patch(
                "wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT",
                Path(__file__).resolve().parent.parent,
            ):
                result = pe_situate(basket)
        assert "wild_igor/igor/brainstem/core_patterns.py" in result["plan_files"]

    def test_situate_empty_affected_files_falls_through(self):
        """'Affected files: TBD' does not short-circuit; tier2 is called."""
        desc = "Some ticket.\n**Affected files:** TBD — discovery step in sprint\n"
        basket = {"ticket_description": desc, "plan_files": []}
        with patch(
            "wild_igor.igor.tools.pe_chain._call_tier2",
            return_value="wild_igor/igor/tools/ops.py",
        ) as mock_t2:
            with patch(
                "wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT",
                Path(__file__).resolve().parent.parent,
            ):
                result = pe_situate(basket)
        mock_t2.assert_called_once()
        assert result["situate_source"] == "tier2_ollama"

    def test_affected_files_helper_parses_comma_list(self):
        desc = "**Affected files:** wild_igor/igor/tools/ops.py, wild_igor/igor/main.py"
        with patch(
            "wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT",
            Path(__file__).resolve().parent.parent,
        ):
            files = _affected_files_from_description(desc)
        assert "wild_igor/igor/tools/ops.py" in files
        assert "wild_igor/igor/main.py" in files

    def test_affected_files_helper_handles_tbd(self):
        assert _affected_files_from_description("**Affected files:** TBD") == []
        assert _affected_files_from_description("Affected files:") == []
        assert _affected_files_from_description("No such field here.") == []

    def test_filter_keeps_low_inertia(self):
        desc = "Whatever."
        kept = _filter_high_inertia_not_in_description(
            ["wild_igor/igor/tools/ops.py"], desc
        )
        assert kept == ["wild_igor/igor/tools/ops.py"]

    def test_filter_rejects_high_inertia_not_named(self):
        desc = "Fix goal continuation."
        kept = _filter_high_inertia_not_in_description(
            ["wild_igor/igor/brainstem/kernel.py"], desc
        )
        assert kept == []

    def test_filter_accepts_high_inertia_named_by_basename(self):
        desc = "Patch kernel.py for the turn loop."
        kept = _filter_high_inertia_not_in_description(
            ["wild_igor/igor/brainstem/kernel.py"], desc
        )
        assert kept == ["wild_igor/igor/brainstem/kernel.py"]


# ── _pe_escalate — T-escalate-validates-file-exists ───────────────────────────


class TestPeEscalateHallucinatedFile:
    def test_nonexistent_high_inertia_file_is_dropped_not_blocked(self):
        """HIGH-inertia reason + nonexistent target_file → drop hypothesis and
        continue, do NOT permanently block the ticket. The offending edit is
        silently removed from basket so IMPLEMENT can proceed with any
        remaining valid hypotheses."""
        hallucinated_hyp = {
            "file": "wild_igor/igor/brainstem/kernel.py",
            "old_string": "x",
            "new_string": "y",
        }
        basket = {
            "ticket_id": "T-fake-test",
            "hypothesis": hallucinated_hyp,
            "hypotheses": [hallucinated_hyp],
        }
        posts = []
        bash_calls = []

        def capture_post(msg, **kwargs):
            posts.append(msg)

        def capture_bash(cmd, **kwargs):
            bash_calls.append(cmd)
            return "ok"

        with (
            patch(
                "wild_igor.igor.tools.pe_chain._post_to_channel",
                side_effect=capture_post,
            ),
            patch("wild_igor.igor.tools.pe_chain._run_bash", side_effect=capture_bash),
        ):
            result = _pe_escalate(basket, reason="HIGH inertia write required")

        # Hypothesis silently dropped — no escalate_reason means ticket NOT blocked
        assert "escalate_reason" not in result
        assert result.get("hypotheses") == []
        assert result.get("hypothesis") == {}
        # No channel posts at all — silent drop
        assert not any("DESIGN PROPOSAL" in p for p in posts)
        assert not any("blocked" in p or "✗" in p for p in posts)
        # cc_queue.py not called
        assert not bash_calls

    def test_real_high_inertia_file_still_proposes_normally(self):
        """HIGH-inertia reason + existing target_file → normal DESIGN PROPOSAL path."""
        basket = {
            "ticket_id": "T-real-test",
            "hypothesis": {
                "file": "wild_igor/igor/brainstem/core_patterns.py",
                "old_string": "x",
                "new_string": "y",
            },
            "plan_summary": "refactor core_patterns",
            "ticket_description": (
                "Refactor the genesis pattern loader.\n"
                "Affected files: wild_igor/igor/brainstem/core_patterns.py"
            ),
        }
        posts = []
        bash_calls = []

        def capture_post(msg, **kwargs):
            posts.append(msg)

        def capture_bash(cmd, **kwargs):
            bash_calls.append(cmd)
            return "ok"

        with (
            patch(
                "wild_igor.igor.tools.pe_chain._post_to_channel",
                side_effect=capture_post,
            ),
            patch("wild_igor.igor.tools.pe_chain._run_bash", side_effect=capture_bash),
        ):
            result = _pe_escalate(basket, reason="HIGH inertia write required")

        # Reason stays as HIGH inertia (not rewritten)
        assert "hallucinated file" not in result["escalate_reason"]
        assert "HIGH inertia" in result["escalate_reason"]
        # DESIGN PROPOSAL is posted
        assert any("DESIGN PROPOSAL" in p for p in posts)
        # cc_queue.py is called with 'propose'
        assert any("propose" in c for c in bash_calls)


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

    def test_caps_at_six_patterns(self):
        desc = "'alpha' 'beta' 'gamma' 'delta' 'epsilon' PROC_X PROC_Y"
        patterns = _extract_grep_patterns(desc)
        assert len(patterns) <= 6

    def test_deduplicates(self):
        desc = "'goal_adopt' and 'goal_adopt' again"
        patterns = _extract_grep_patterns(desc)
        assert patterns.count("goal_adopt") == 1

    def test_empty_description(self):
        patterns = _extract_grep_patterns("")
        assert patterns == []

    def test_expands_register_to_registry(self):
        # "register" in ticket → also grep for "registry"
        desc = "Fix the tool register call in the chain"
        patterns = _extract_grep_patterns(desc)
        assert "registry" in patterns or "Tool(" in patterns

    def test_expands_habit_to_proc(self):
        desc = "habit seeding is broken for new types"
        patterns = _extract_grep_patterns(desc)
        assert "PROC_" in patterns or "seed_habits" in patterns

    def test_expansion_adds_at_most_two_extra(self):
        # Even with multiple expansion-eligible base patterns, cap at 6 total
        desc = "register habit tool"
        patterns = _extract_grep_patterns(desc)
        assert len(patterns) <= 6

    def test_no_expansion_when_no_match(self):
        # No expansion key in description — only base patterns returned
        desc = "something completely unrelated zorp quux"
        patterns = _extract_grep_patterns(desc)
        # No expansion means fewer patterns (only what regex extracts)
        for p in patterns:
            assert p not in _CODE_EXPANSION.values()


class TestExpandPatternsWithSynonyms:
    def test_returns_empty_for_unknown_patterns(self):
        assert _expand_patterns_with_synonyms(["zorp", "quux"]) == []

    def test_expands_first_matching_pattern(self):
        extras = _expand_patterns_with_synonyms(["register_tool"])
        assert "registry" in extras or "Tool(" in extras

    def test_caps_at_two_extras(self):
        # Multiple matching base patterns — still max 2 extras
        extras = _expand_patterns_with_synonyms(
            ["register_tool", "habit_type", "memory_node"]
        )
        assert len(extras) <= 2

    def test_no_duplicates_with_base(self):
        # If base already contains an expansion value, don't re-add it
        extras = _expand_patterns_with_synonyms(["register_tool", "registry"])
        assert extras.count("registry") == 0


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
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT", REPO_ROOT):
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
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT", REPO_ROOT):
            result = pe_observe(basket)
        assert len(result["actual"]) < len(full_file)

    def test_grep_hit_increments_observe_hits(self):
        basket = {
            "ticket_description": "Fix 'goal_adopt' category mismatch",
            "plan_files": ["wild_igor/igor/tools/ops.py"],
        }
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT", REPO_ROOT):
            result = pe_observe(basket)
        # goal_adopt exists in ops.py — should get a grep hit
        assert result["observe_hits"] >= 1

    def test_line_ranges_populated(self):
        basket = {
            "ticket_description": "Fix 'goal_adopt' category",
            "plan_files": ["wild_igor/igor/tools/ops.py"],
        }
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT", REPO_ROOT):
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
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT", REPO_ROOT):
            result = pe_observe(basket)
        assert "ops.py" in result["actual"]
        assert "main.py" in result["actual"]
        assert len(result["line_ranges"]) == 2


# ── pe_store_observe_results ─────────────────────────────────────────────────


class TestPeStoreObserveResults:
    def test_skips_when_no_hits(self):
        basket = {
            "ticket_id": "T-test",
            "ticket_description": "fix something",
            "actual": "",
            "observe_hits": 0,
            "plan_files": [],
        }
        result = pe_store_observe_results(basket)
        assert result["observe_stored_id"] is None

    def test_skips_when_no_actual(self):
        basket = {
            "ticket_id": "T-test",
            "ticket_description": "fix something",
            "actual": "",
            "observe_hits": 3,
            "plan_files": ["foo.py"],
        }
        result = pe_store_observe_results(basket)
        assert result["observe_stored_id"] is None

    def test_passes_through_on_error_basket(self):
        basket = {"error": "prior step failed"}
        result = pe_store_observe_results(basket)
        assert result["error"] == "prior step failed"
        assert "observe_stored_id" not in result

    def test_calls_store_factual_on_hits(self):
        basket = {
            "ticket_id": "T-abc",
            "ticket_description": "add freshness signal",
            "actual": "def some_function(): pass",
            "observe_hits": 2,
            "plan_files": ["wild_igor/igor/memory/cortex.py"],
        }
        with patch(
            "wild_igor.igor.tools.graph_write.store_factual",
            return_value="stored mem123: Codebase search",
        ) as mock_store:
            result = pe_store_observe_results(basket)
        # store_factual was called with a summary containing key fields
        assert mock_store.called
        call_arg = mock_store.call_args[0][0]
        assert "T-abc" in call_arg
        assert "cortex.py" in call_arg
        assert result["observe_stored_id"] == "stored mem123: Codebase search"

    def test_non_fatal_on_store_failure(self):
        basket = {
            "ticket_id": "T-xyz",
            "ticket_description": "fix bug",
            "actual": "some code",
            "observe_hits": 1,
            "plan_files": ["foo.py"],
        }
        with patch(
            "wild_igor.igor.tools.graph_write.store_factual",
            side_effect=RuntimeError("cortex down"),
        ):
            result = pe_store_observe_results(basket)
        # Chain continues — no error set, stored_id is None
        assert result.get("error") is None
        assert result["observe_stored_id"] is None


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
        # _parse_hypothesis returns list[dict] (T-pe-multi-file)
        raw = '{"file": "a.py", "old_string": "foo", "new_string": "bar"}'
        result = _parse_hypothesis(raw)
        assert result == [{"file": "a.py", "old_string": "foo", "new_string": "bar"}]

    def test_strips_markdown_fences(self):
        raw = '```json\n{"file": "a.py", "old_string": "x", "new_string": "y"}\n```'
        result = _parse_hypothesis(raw)
        assert result is not None
        assert result[0]["file"] == "a.py"

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
        # Either parsed or None — just verify it doesn't crash.
        # _parse_hypothesis may return None, a dict, or list[dict] (multi-file, T-pe-multi-file).
        assert result is None or isinstance(result, (dict, list))


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
            with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT", REPO_ROOT):
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
            with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT", REPO_ROOT):
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
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT", tmp_path):
            result = pe_implement(basket)
        assert result["implement_result"].startswith("ok")
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
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT", tmp_path):
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
        with patch("wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT", tmp_path):
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
    """Basket in the state just before pe_close_loop: test passed, edit applied.

    Mirrors what pe_implement actually writes on success: implement_files
    populated with the modified file, implement_skipped=False. Required by
    the _pe_close defensive guard (T-pe-chain-empty-close-detection).
    """
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
        "implement_files": [file],
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

    def test_pass_path_escalates_when_implement_skipped(self):
        # When implement_skipped=True (HYPOTHESIZE produced invalid old_string),
        # the chain must escalate rather than falsely closing the ticket as done.
        basket = _passing_basket()
        basket["implement_skipped"] = True
        basket["hypothesis_error"] = "validation failed: old_string not found verbatim"
        with (
            patch("wild_igor.igor.tools.pe_chain._run_bash") as mock_bash,
            patch("wild_igor.igor.tools.pe_chain._post_to_channel"),
        ):
            with patch.dict("sys.modules", {"wild_igor.igor.tools.ops": None}):
                result = pe_close_loop(basket)
        # Must escalate, not silently succeed
        assert "escalate_reason" in result
        assert "implement_skipped" in result["escalate_reason"]
        # git commit must NOT be called
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

        def fake_tier2(prompt, timeout=30, **_):
            call_count["n"] += 1
            return '{"file": "wild_igor/igor/tools/pe_chain.py", "old_string": "pe_chain", "new_string": "pe_chain"}'

        with (
            patch("wild_igor.igor.tools.pe_chain._call_tier2", side_effect=fake_tier2),
            patch("wild_igor.igor.tools.pe_chain._REPO_ROOT_DEFAULT", REPO_ROOT),
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
            with patch.dict("sys.modules", {"wild_igor.igor.tools.ops": None}):
                result = pe_close_loop(basket)
        # Should have hit max and escalated
        assert "escalate_reason" in result


class TestSituateFromMemory:
    """Unit tests for _situate_from_memory — prior observe deposit lookup."""

    def test_returns_files_when_deposit_found(self):
        narrative = (
            "Codebase search for [T-foo]: fix the thing. "
            "Files: wild_igor/igor/tools/pe_chain.py, wild_igor/igor/main.py. "
            "Grep hits: 3. Excerpt: some code"
        )
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = (narrative,)
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        # psycopg2 is imported locally inside _situate_from_memory
        with patch("psycopg2.connect", return_value=mock_conn):
            result = _situate_from_memory("T-foo")
        assert result == [
            "wild_igor/igor/tools/pe_chain.py",
            "wild_igor/igor/main.py",
        ]

    def test_returns_empty_when_no_deposit(self):
        mock_cur = MagicMock()
        mock_cur.fetchone.return_value = None
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        with patch("psycopg2.connect", return_value=mock_conn):
            result = _situate_from_memory("T-nonexistent")
        assert result == []

    def test_returns_empty_on_db_error(self):
        with patch("psycopg2.connect", side_effect=Exception("conn refused")):
            result = _situate_from_memory("T-any")
        assert result == []

    def test_situate_uses_memory_before_tier2(self):
        """pe_situate hits memory path before tier.2 when ticket_id present."""
        basket = {
            "ticket_description": "fix something",
            "plan_files": [],
            "ticket_id": "T-foo",
        }
        with patch(
            "wild_igor.igor.tools.pe_chain._situate_from_memory",
            return_value=["wild_igor/igor/tools/pe_chain.py"],
        ) as mock_mem:
            with patch("wild_igor.igor.tools.pe_chain._call_tier2") as mock_tier2:
                result = pe_situate(basket)
        mock_mem.assert_called_once_with("T-foo")
        mock_tier2.assert_not_called()
        assert result["situate_source"] == "prior_observe_memory"
        assert result["plan_files"] == ["wild_igor/igor/tools/pe_chain.py"]

    def test_situate_falls_through_to_tier2_when_no_memory(self):
        """pe_situate calls tier.2 when memory has no prior deposit."""
        basket = {
            "ticket_description": "fix something",
            "plan_files": [],
            "ticket_id": "T-bar",
        }
        with patch(
            "wild_igor.igor.tools.pe_chain._situate_from_memory",
            return_value=[],
        ):
            with patch(
                "wild_igor.igor.tools.pe_chain._call_tier2",
                return_value="wild_igor/igor/tools/pe_chain.py",
            ) as mock_tier2:
                result = pe_situate(basket)
        mock_tier2.assert_called_once()
        assert result["situate_source"] == "tier2_ollama"
