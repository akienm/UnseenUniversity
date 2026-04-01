"""
Tests for T-deadend-ack-filter: _is_bare_ack() in main.py
Verifies that bare acknowledgments are detected and content-bearing
responses are not suppressed.
"""

import pytest


def _import_is_bare_ack():
    # Import without triggering full Igor boot — pull just the function
    from wild_igor.igor.main import _is_bare_ack

    return _is_bare_ack


class TestIsBareAck:
    """Tests for _is_bare_ack() — the T-deadend-ack-filter utility."""

    @pytest.fixture(autouse=True)
    def _fn(self):
        self.is_bare_ack = _import_is_bare_ack()

    # ── Should return True (bare acks) ────────────────────────────────────────

    def test_fair_on_it(self):
        assert self.is_bare_ack("Fair. On it.") is True

    def test_on_it(self):
        assert self.is_bare_ack("On it.") is True

    def test_got_it(self):
        assert self.is_bare_ack("Got it.") is True

    def test_understood(self):
        assert self.is_bare_ack("Understood.") is True

    def test_sure(self):
        assert self.is_bare_ack("Sure.") is True

    def test_will_do(self):
        assert self.is_bare_ack("Will do.") is True

    def test_noted(self):
        assert self.is_bare_ack("Noted.") is True

    def test_okay(self):
        assert self.is_bare_ack("Okay.") is True

    def test_ok(self):
        assert self.is_bare_ack("Ok.") is True

    def test_acknowledged(self):
        assert self.is_bare_ack("Acknowledged.") is True

    def test_roger(self):
        assert self.is_bare_ack("Roger.") is True

    def test_case_insensitive(self):
        assert self.is_bare_ack("GOT IT.") is True
        assert self.is_bare_ack("got it") is True
        assert self.is_bare_ack("UNDERSTOOD") is True

    def test_with_leading_trailing_whitespace(self):
        assert self.is_bare_ack("  Got it.  ") is True

    def test_exclamation_variant(self):
        assert self.is_bare_ack("Got it!") is True
        assert self.is_bare_ack("Sure!") is True

    # ── Should return False (content-bearing responses) ───────────────────────

    def test_content_after_ack(self):
        assert self.is_bare_ack("Got it. Here's what I found:") is False

    def test_full_sentence(self):
        assert self.is_bare_ack("I'll look into the T-phase-d-ex4 ticket now.") is False

    def test_empty_string(self):
        assert self.is_bare_ack("") is False

    def test_whitespace_only(self):
        assert self.is_bare_ack("   ") is False

    def test_partial_match_not_enough(self):
        # "noted" as part of longer content should not match
        assert self.is_bare_ack("Noted — here's the analysis:") is False

    def test_multiline_content(self):
        assert self.is_bare_ack("Got it.\nHere is the breakdown.") is False

    def test_question(self):
        assert self.is_bare_ack("What do you need?") is False

    def test_substantive_reply(self):
        assert (
            self.is_bare_ack("The ticket T-deadend-ack-filter tracks this bug.")
            is False
        )
