"""
test_action_claim_verifier.py — T-igor-emit-action-confabulation.

Tests the detection-only first pass of action-claim verification. When
Igor's reply contains a phrase like 'I ticketed it' but no evidence
anchor exists in the recent past, the verifier logs CONFAB_CAUGHT and
pushes a high-salience TWM marker.

This catches the exact failure mode from the 2026-04-13 transcript:
Igor said 'The ticket about the privacy-guard halt is already in the
shared database' when no such ticket was ever written.

Tests cover:
  - detect_action_claims finds ticket-filing phrases
  - detect skips innocuous text
  - find_evidence picks up recent cc_queue ticket updates (Postgres)
  - find_evidence picks up recent RESOLVED|/TOOL_RESULT| ring entries
  - check_response is no-op when no claims are present
  - check_response is no-op when claims present AND evidence present
  - check_response logs + TWM-pushes when claims present AND no evidence
  - check_response NEVER raises and NEVER modifies response_text
"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── detect_action_claims ─────────────────────────────────────────────────────


def test_detect_finds_i_ticketed_it():
    from devices.igor.cognition.action_claim_verifier import detect_action_claims

    claims = detect_action_claims("I ticketed it. The defect is recorded.")
    assert len(claims) >= 1
    assert "ticketed" in claims[0].lower()


def test_detect_finds_the_ticket_is_already_in_database():
    """The exact phrase from the 2026-04-13 transcript."""
    from devices.igor.cognition.action_claim_verifier import detect_action_claims

    text = (
        "The ticket about the privacy-guard halt is already in the shared "
        "database — Claude has it."
    )
    claims = detect_action_claims(text)
    assert len(claims) >= 1


def test_detect_finds_i_filed_that():
    from devices.igor.cognition.action_claim_verifier import detect_action_claims

    claims = detect_action_claims("done — I filed that as a ticket.")
    assert len(claims) >= 1


def test_detect_finds_i_added_it_to_the_queue():
    from devices.igor.cognition.action_claim_verifier import detect_action_claims

    claims = detect_action_claims("I added that to the queue.")
    assert len(claims) >= 1


def test_detect_finds_i_committed_it():
    from devices.igor.cognition.action_claim_verifier import detect_action_claims

    claims = detect_action_claims("I committed that change.")
    assert len(claims) >= 1


def test_detect_skips_innocuous_text():
    from devices.igor.cognition.action_claim_verifier import detect_action_claims

    assert detect_action_claims("Hello, what would you like to tackle?") == []
    assert detect_action_claims("That's an interesting question.") == []
    assert detect_action_claims("") == []
    assert detect_action_claims(None) == []  # type: ignore


def test_detect_skips_passive_voice_without_claim():
    """We're catching 'I did X' style claims, not general references to
    tickets existing. The pattern requires Igor saying he just did
    something — past tense, first person, completed action."""
    from devices.igor.cognition.action_claim_verifier import detect_action_claims

    # These describe state without claiming Igor just did them
    benign = [
        "There are several open tickets in the queue.",
        "Tickets exist for both bugs.",
        "Should we file a ticket for that?",
    ]
    for text in benign:
        assert detect_action_claims(text) == [], f"false positive on: {text!r}"


# ── find_evidence ────────────────────────────────────────────────────────────


def test_find_evidence_returns_dict_shape():
    from devices.igor.cognition.action_claim_verifier import find_evidence

    cortex = MagicMock()
    cortex.search_ring = MagicMock(return_value=[])

    result = find_evidence(cortex)
    assert "queue_modified" in result
    assert "tool_results_count" in result
    assert "any_evidence" in result
    assert isinstance(result["any_evidence"], bool)


def test_find_evidence_picks_up_recent_queue_write():
    """When _cc_queue_recently_modified returns True, queue_modified=True."""
    from devices.igor.cognition.action_claim_verifier import find_evidence

    cortex = MagicMock()
    cortex.search_ring = MagicMock(return_value=[])

    with patch(
        "devices.igor.cognition.action_claim_verifier._cc_queue_recently_modified",
        return_value=True,
    ):
        result = find_evidence(cortex)
    assert result["queue_modified"] is True
    assert result["any_evidence"] is True


def test_find_evidence_no_evidence_when_quiet():
    """When _cc_queue_recently_modified returns False and no ring results, any_evidence=False."""
    from devices.igor.cognition.action_claim_verifier import find_evidence

    cortex = MagicMock()
    cortex.search_ring = MagicMock(return_value=[])

    with patch(
        "devices.igor.cognition.action_claim_verifier._cc_queue_recently_modified",
        return_value=False,
    ):
        result = find_evidence(cortex)
    assert result["queue_modified"] is False
    assert result["any_evidence"] is False


# ── check_response ───────────────────────────────────────────────────────────


def test_check_response_noop_on_clean_text():
    from devices.igor.cognition.action_claim_verifier import check_response

    cortex = MagicMock()
    cortex.search_ring = MagicMock(return_value=[])
    cortex.write_ring = MagicMock()
    cortex.twm_push = MagicMock()

    unverified = check_response(cortex, "Hello, what would you like to tackle?")
    assert unverified == []
    cortex.write_ring.assert_not_called()
    cortex.twm_push.assert_not_called()


def test_check_response_noop_when_claim_has_evidence():
    """Action claim present, queue recently updated → verified, no warning fired."""
    from devices.igor.cognition.action_claim_verifier import check_response

    cortex = MagicMock()
    cortex.search_ring = MagicMock(return_value=[])
    cortex.write_ring = MagicMock()
    cortex.twm_push = MagicMock()

    with patch(
        "devices.igor.cognition.action_claim_verifier._cc_queue_recently_modified",
        return_value=True,
    ):
        unverified = check_response(cortex, "I ticketed it. Done.", turn_id="testturn")
    assert unverified == []
    cortex.write_ring.assert_not_called()
    cortex.twm_push.assert_not_called()


def test_check_response_logs_and_pushes_when_unverified():
    """The exact 2026-04-13 case: claim present, no evidence anchor —
    should log CONFAB_CAUGHT and push high-salience TWM marker."""
    from devices.igor.cognition.action_claim_verifier import check_response

    cortex = MagicMock()
    cortex.search_ring = MagicMock(return_value=[])
    cortex.write_ring = MagicMock()
    cortex.twm_push = MagicMock()
    cortex.read_ring_memory = MagicMock(
        return_value=[]
    )  # no prior confab — don't suppress

    with patch(
        "devices.igor.cognition.action_claim_verifier._cc_queue_recently_modified",
        return_value=False,
    ):
        unverified = check_response(
            cortex,
            "The ticket about the privacy-guard halt is already in the shared database.",
            turn_id="confabturn",
            thread_id="web:shared",
        )
    assert len(unverified) >= 1
    # Both side effects fired
    cortex.write_ring.assert_called_once()
    cortex.twm_push.assert_called_once()
    # The TWM push is at high salience and the right category
    push_kwargs = cortex.twm_push.call_args.kwargs
    assert push_kwargs["category"] == "confab_caught"
    assert push_kwargs["salience"] >= 0.85
    assert "CONFAB_CAUGHT" in push_kwargs["content_csb"]


def test_check_response_never_raises():
    """Pass garbage to check_response and verify it returns gracefully."""
    from devices.igor.cognition.action_claim_verifier import check_response

    # Cortex that explodes on every method
    bad_cortex = MagicMock()
    bad_cortex.search_ring = MagicMock(side_effect=RuntimeError("boom"))
    bad_cortex.write_ring = MagicMock(side_effect=RuntimeError("boom"))
    bad_cortex.twm_push = MagicMock(side_effect=RuntimeError("boom"))

    try:
        result = check_response(bad_cortex, "I ticketed it.", turn_id="explodes")
    except Exception as e:
        pytest.fail(f"check_response should never raise — got {e}")
    # We get a list back (empty or otherwise)
    assert isinstance(result, list)


def test_check_response_does_not_modify_text():
    """Detection-only sprint — response_text is never mutated. The reply
    goes through verbatim regardless of whether claims are caught."""
    from devices.igor.cognition.action_claim_verifier import check_response

    cortex = MagicMock()
    cortex.search_ring = MagicMock(return_value=[])
    cortex.write_ring = MagicMock()
    cortex.twm_push = MagicMock()

    original = "I ticketed it. The defect is recorded in the database."
    # check_response returns a list of unverified claims, NOT a modified text
    unverified = check_response(cortex, original, turn_id="immutable")
    # The function signature returns list[str], not str
    assert isinstance(unverified, list)
    # And the input string is untouched (Python strings are immutable
    # but this is the contract assertion: no in-place modification path)
    assert original == "I ticketed it. The defect is recorded in the database."


# ── Phase 2: Active suppression (T-active-suppression-action-claims) ────────


class TestSuppressFalseClaims:
    """suppress_false_claims strips unverified claims from response text."""

    def test_strips_single_claim(self):
        from devices.igor.cognition.action_claim_verifier import (
            suppress_false_claims,
        )

        text = "Sure! I've just ticketed it. Let me know if you need anything else."
        claims = ["I've just ticketed it"]
        result = suppress_false_claims(text, claims)
        assert "ticketed" not in result
        assert "Let me know" in result

    def test_strips_multiple_claims(self):
        from devices.igor.cognition.action_claim_verifier import (
            suppress_false_claims,
        )

        text = "I've filed it. I also committed it. Both are done."
        claims = ["I've filed it", "committed it"]
        result = suppress_false_claims(text, claims)
        assert "filed" not in result
        assert "committed" not in result
        assert "done" in result

    def test_preserves_text_when_no_claims(self):
        from devices.igor.cognition.action_claim_verifier import (
            suppress_false_claims,
        )

        text = "Here is a perfectly normal response."
        result = suppress_false_claims(text, [])
        assert result == text

    def test_never_returns_empty(self):
        from devices.igor.cognition.action_claim_verifier import (
            suppress_false_claims,
        )

        text = "I've just ticketed it."
        claims = ["I've just ticketed it"]
        result = suppress_false_claims(text, claims)
        # Should return original rather than empty
        assert len(result) > 0

    def test_cleans_double_spaces(self):
        from devices.igor.cognition.action_claim_verifier import (
            suppress_false_claims,
        )

        text = "Yes, I've filed it in the database. Moving on."
        claims = ["I've filed it in the database"]
        result = suppress_false_claims(text, claims)
        assert "  " not in result

    def test_handles_none_inputs(self):
        from devices.igor.cognition.action_claim_verifier import (
            suppress_false_claims,
        )

        assert suppress_false_claims("", ["claim"]) == ""
        assert suppress_false_claims("text", []) == "text"
        assert suppress_false_claims("text", None) == "text"


# ── refractory window ─────────────────────────────────────────────────────────


class TestRefractoryWindow:
    """Consecutive-turn suppression for coherence and confab detectors."""

    def test_coherence_second_fire_suppressed_when_ring_has_recent_failure(self):
        from unittest.mock import MagicMock
        from devices.igor.cognition.response_coherence_inhibitor import (
            check_coherence,
        )

        cortex = MagicMock()
        cortex.write_ring = MagicMock()
        cortex.twm_push = MagicMock()
        # Simulate ring already has a recent coherence_failure for this thread
        cortex.read_ring_memory.return_value = [{"category": "coherence_failure"}]

        prompt = "neurons cortex hippocampus amygdala synapse biology dendrites prefrontal thalamus basal ganglia"
        response = "configure threshold preparse stage token enable disable pipeline queue handler"

        result = check_coherence(cortex, prompt, response, thread_id="thread-A")
        assert result["reason"] == "refractory_suppressed"
        assert result["flagged"] is True
        cortex.twm_push.assert_not_called()

    def test_coherence_first_fire_not_suppressed_when_ring_empty(self):
        from unittest.mock import MagicMock
        from devices.igor.cognition.response_coherence_inhibitor import (
            check_coherence,
        )

        cortex = MagicMock()
        cortex.read_ring_memory.return_value = []  # no prior failures

        prompt = "neurons cortex hippocampus amygdala synapse biology dendrites prefrontal thalamus basal ganglia"
        response = "configure threshold preparse stage token enable disable pipeline queue handler"

        result = check_coherence(cortex, prompt, response, thread_id="thread-B")
        assert result["reason"] != "refractory_suppressed"
        cortex.twm_push.assert_called_once()

    def test_confab_second_fire_suppressed_when_ring_has_recent_confab(self):
        from unittest.mock import MagicMock, patch
        from devices.igor.cognition.action_claim_verifier import check_response

        cortex = MagicMock()
        cortex.read_ring_memory.return_value = [{"category": "confab_caught"}]

        text = "I've just ticketed it in the database."
        with patch(
            "devices.igor.cognition.action_claim_verifier._cc_queue_recently_modified",
            return_value=False,
        ), patch(
            "devices.igor.cognition.action_claim_verifier._recent_tool_results",
            return_value=[],
        ):
            claims = check_response(cortex, text, thread_id="thread-C")

        assert len(claims) > 0  # still returns claims (logged)
        cortex.twm_push.assert_not_called()  # TWM suppressed
