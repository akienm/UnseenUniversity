"""T-pe-chain-inertia-gate-hallucinated-target — scope cross-check tests.

The HIGH-inertia escalation gate must reject a hypothesized target file that
tier2 hallucinated (e.g. brainstem/core_patterns.py) when the ticket's
'Affected files:' section names a completely different file. Before this fix,
six Igor tickets stuck in awaiting_approval with identical bogus proposals
because the gate only checked filesystem existence of the hypothesized path.
"""

from __future__ import annotations

import pytest

from wild_igor.igor.tools.pe_chain import (
    _affected_files_from_description,
    _filter_high_inertia_not_in_description,
)


class TestAffectedFilesParsing:
    """_affected_files_from_description validates paths exist — confirmed
    upstream behavior that affects downstream reliance."""

    def test_parses_existing_path(self):
        # Use a known-existing repo path so _parse_file_list accepts it
        desc = (
            "Fix the thing.\n"
            "**Affected files:** wild_igor/igor/tools/pe_chain.py\n"
            "More body.\n"
        )
        files = _affected_files_from_description(desc)
        assert "wild_igor/igor/tools/pe_chain.py" in files

    def test_empty_description_returns_empty(self):
        assert _affected_files_from_description("") == []

    def test_missing_field_returns_empty(self):
        assert _affected_files_from_description("no such field") == []

    def test_tbd_returns_empty(self):
        desc = "**Affected files:** TBD — discovery step in sprint"
        assert _affected_files_from_description(desc) == []

    def test_new_file_path_is_filtered_by_existence_check(self):
        # Document known limitation: new-file tickets don't populate this list.
        # The gate compensates via description-text match in
        # _filter_high_inertia_not_in_description.
        desc = "**Affected files:** lab/claudecode/brand_new_never_existed.py"
        assert _affected_files_from_description(desc) == []


class TestHighInertiaScopeFilter:
    def test_rejects_brainstem_when_not_in_description(self):
        desc = (
            "New CI grep check for no-sqlite.\n"
            "**Affected files:** lab/claudecode/check_no_sqlite.py\n"
        )
        kept = _filter_high_inertia_not_in_description(
            ["wild_igor/igor/brainstem/core_patterns.py"], desc
        )
        assert kept == []

    def test_keeps_brainstem_when_named_in_description(self):
        desc = (
            "Refactor core_patterns.py to remove dead branches.\n"
            "**Affected files:** wild_igor/igor/brainstem/core_patterns.py\n"
        )
        kept = _filter_high_inertia_not_in_description(
            ["wild_igor/igor/brainstem/core_patterns.py"], desc
        )
        assert kept == ["wild_igor/igor/brainstem/core_patterns.py"]

    def test_keeps_low_inertia_regardless(self):
        desc = "Tiny change."
        kept = _filter_high_inertia_not_in_description(
            ["lab/claudecode/check_no_sqlite.py"], desc
        )
        assert kept == ["lab/claudecode/check_no_sqlite.py"]

    def test_keeps_by_basename_match(self):
        desc = (
            "Update core_patterns to add the new engram.\n"
            "Body mentions core_patterns.py by basename only."
        )
        kept = _filter_high_inertia_not_in_description(
            ["wild_igor/igor/brainstem/core_patterns.py"], desc
        )
        assert "wild_igor/igor/brainstem/core_patterns.py" in kept


class TestEscalateGateIntegration:
    """End-to-end: the _pe_escalate path must block the hallucinated proposal."""

    def _escalate_with(self, basket, reason):
        """Call _pe_escalate with the test basket. Returns the mutated basket."""
        from wild_igor.igor.tools import pe_chain

        # The function is module-private (underscore-prefixed) but importable.
        return pe_chain._pe_escalate(basket, reason)

    def test_hallucinated_high_inertia_outside_scope_is_rewritten(self, monkeypatch):
        """Key regression: T-no-sqlite-enforcement-shaped ticket with a
        brainstem hallucination must NOT get proposed. Gate should rewrite
        reason and drop is_high_inertia so the proposal path is skipped."""
        from wild_igor.igor.tools import pe_chain

        posted = []
        monkeypatch.setattr(
            pe_chain,
            "_post_to_channel",
            lambda msg, **_: posted.append(msg),
        )
        monkeypatch.setattr(pe_chain, "_run_bash", lambda *a, **kw: None)

        basket = {
            "ticket_id": "T-test-hallucinated-target",
            "ticket_description": (
                "Add a CI grep-check for no-sqlite patterns.\n"
                "**Affected files:** lab/claudecode/check_no_sqlite.py\n"
            ),
            "hypothesis": {
                "file": "wild_igor/igor/brainstem/core_patterns.py",
                "old_string": "x",
                "new_string": "y",
            },
            "plan_summary": "refactor core patterns",
        }

        result = self._escalate_with(basket, "HIGH inertia target")

        # The escalate_reason should have been rewritten to flag the hallucinated scope
        assert "hallucinated HIGH-inertia target" in result["escalate_reason"]
        # No design proposal should have been posted to the channel
        assert not any("DESIGN PROPOSAL" in m for m in posted)

    def test_legitimate_high_inertia_in_scope_still_proposes(self, monkeypatch):
        """When the ticket explicitly names brainstem/core_patterns.py, the
        gate still fires as before — the filter keeps the path through."""
        from wild_igor.igor.tools import pe_chain

        posted = []
        monkeypatch.setattr(
            pe_chain,
            "_post_to_channel",
            lambda msg, **_: posted.append(msg),
        )
        monkeypatch.setattr(pe_chain, "_run_bash", lambda *a, **kw: None)

        basket = {
            "ticket_id": "T-legit-brainstem-edit",
            "ticket_description": (
                "Refactor core_patterns.py to remove a dead branch.\n"
                "**Affected files:** wild_igor/igor/brainstem/core_patterns.py\n"
            ),
            "hypothesis": {
                "file": "wild_igor/igor/brainstem/core_patterns.py",
                "old_string": "x",
                "new_string": "y",
            },
            "plan_summary": "remove dead branch in SITUATE",
        }

        result = self._escalate_with(basket, "HIGH inertia target")

        # Reason should NOT have been rewritten as hallucinated
        assert "hallucinated" not in result["escalate_reason"]
        # Design proposal should have been posted normally
        assert any("DESIGN PROPOSAL" in m for m in posted)
