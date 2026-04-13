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
  - find_evidence picks up cc_queue.json modifications (file mtime)
  - find_evidence picks up recent RESOLVED|/TOOL_RESULT| ring entries
  - check_response is no-op when no claims are present
  - check_response is no-op when claims present AND evidence present
  - check_response logs + TWM-pushes when claims present AND no evidence
  - check_response NEVER raises and NEVER modifies response_text
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── detect_action_claims ─────────────────────────────────────────────────────


def test_detect_finds_i_ticketed_it():
    from wild_igor.igor.cognition.action_claim_verifier import detect_action_claims

    claims = detect_action_claims("I ticketed it. The defect is recorded.")
    assert len(claims) >= 1
    assert "ticketed" in claims[0].lower()


def test_detect_finds_the_ticket_is_already_in_database():
    """The exact phrase from the 2026-04-13 transcript."""
    from wild_igor.igor.cognition.action_claim_verifier import detect_action_claims

    text = (
        "The ticket about the privacy-guard halt is already in the shared "
        "database — Claude has it."
    )
    claims = detect_action_claims(text)
    assert len(claims) >= 1


def test_detect_finds_i_filed_that():
    from wild_igor.igor.cognition.action_claim_verifier import detect_action_claims

    claims = detect_action_claims("done — I filed that as a ticket.")
    assert len(claims) >= 1


def test_detect_finds_i_added_it_to_the_queue():
    from wild_igor.igor.cognition.action_claim_verifier import detect_action_claims

    claims = detect_action_claims("I added that to the queue.")
    assert len(claims) >= 1


def test_detect_finds_i_committed_it():
    from wild_igor.igor.cognition.action_claim_verifier import detect_action_claims

    claims = detect_action_claims("I committed that change.")
    assert len(claims) >= 1


def test_detect_skips_innocuous_text():
    from wild_igor.igor.cognition.action_claim_verifier import detect_action_claims

    assert detect_action_claims("Hello, what would you like to tackle?") == []
    assert detect_action_claims("That's an interesting question.") == []
    assert detect_action_claims("") == []
    assert detect_action_claims(None) == []  # type: ignore


def test_detect_skips_passive_voice_without_claim():
    """We're catching 'I did X' style claims, not general references to
    tickets existing. The pattern requires Igor saying he just did
    something — past tense, first person, completed action."""
    from wild_igor.igor.cognition.action_claim_verifier import detect_action_claims

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
    from wild_igor.igor.cognition.action_claim_verifier import find_evidence

    cortex = MagicMock()
    cortex.search_ring = MagicMock(return_value=[])

    result = find_evidence(cortex)
    assert "queue_modified" in result
    assert "tool_results_count" in result
    assert "any_evidence" in result
    assert isinstance(result["any_evidence"], bool)


def test_find_evidence_picks_up_recent_queue_write():
    """Touch cc_queue.json so its mtime is now, then verify
    queue_modified=True."""
    from wild_igor.igor.cognition.action_claim_verifier import find_evidence

    queue_path = Path.home() / ".TheIgors" / "cc_channel" / "queue.json"
    if not queue_path.exists():
        pytest.skip("cc_queue.json not present in this environment")
    # Touch mtime to now
    os.utime(queue_path, None)

    cortex = MagicMock()
    cortex.search_ring = MagicMock(return_value=[])

    result = find_evidence(cortex)
    assert result["queue_modified"] is True
    assert result["any_evidence"] is True


def test_find_evidence_no_evidence_when_quiet():
    """Mock cortex with no ring results AND temporarily back-date the
    queue file mtime past the window."""
    from wild_igor.igor.cognition.action_claim_verifier import (
        find_evidence,
        _EVIDENCE_WINDOW_SEC,
    )

    queue_path = Path.home() / ".TheIgors" / "cc_channel" / "queue.json"
    if not queue_path.exists():
        pytest.skip("cc_queue.json not present in this environment")

    # Save original mtime, back-date past the window
    orig_atime = queue_path.stat().st_atime
    orig_mtime = queue_path.stat().st_mtime
    long_ago = time.time() - _EVIDENCE_WINDOW_SEC - 60
    os.utime(queue_path, (orig_atime, long_ago))

    try:
        cortex = MagicMock()
        cortex.search_ring = MagicMock(return_value=[])

        result = find_evidence(cortex)
        assert result["queue_modified"] is False
        assert result["any_evidence"] is False
    finally:
        # Restore original mtime
        os.utime(queue_path, (orig_atime, orig_mtime))


# ── check_response ───────────────────────────────────────────────────────────


def test_check_response_noop_on_clean_text():
    from wild_igor.igor.cognition.action_claim_verifier import check_response

    cortex = MagicMock()
    cortex.search_ring = MagicMock(return_value=[])
    cortex.write_ring = MagicMock()
    cortex.twm_push = MagicMock()

    unverified = check_response(cortex, "Hello, what would you like to tackle?")
    assert unverified == []
    cortex.write_ring.assert_not_called()
    cortex.twm_push.assert_not_called()


def test_check_response_noop_when_claim_has_evidence():
    """Action claim present, but cc_queue.json mtime is fresh → verified,
    no warning fired."""
    from wild_igor.igor.cognition.action_claim_verifier import check_response

    queue_path = Path.home() / ".TheIgors" / "cc_channel" / "queue.json"
    if not queue_path.exists():
        pytest.skip("cc_queue.json not present in this environment")
    os.utime(queue_path, None)  # fresh mtime

    cortex = MagicMock()
    cortex.search_ring = MagicMock(return_value=[])
    cortex.write_ring = MagicMock()
    cortex.twm_push = MagicMock()

    unverified = check_response(cortex, "I ticketed it. Done.", turn_id="testturn")
    assert unverified == []
    cortex.write_ring.assert_not_called()
    cortex.twm_push.assert_not_called()


def test_check_response_logs_and_pushes_when_unverified():
    """The exact 2026-04-13 case: claim present, no evidence anchor —
    should log CONFAB_CAUGHT and push high-salience TWM marker."""
    from wild_igor.igor.cognition.action_claim_verifier import (
        check_response,
        _EVIDENCE_WINDOW_SEC,
    )

    queue_path = Path.home() / ".TheIgors" / "cc_channel" / "queue.json"
    if not queue_path.exists():
        pytest.skip("cc_queue.json not present in this environment")

    # Back-date queue file past the window so no evidence is found
    orig_atime = queue_path.stat().st_atime
    orig_mtime = queue_path.stat().st_mtime
    long_ago = time.time() - _EVIDENCE_WINDOW_SEC - 60
    os.utime(queue_path, (orig_atime, long_ago))

    try:
        cortex = MagicMock()
        cortex.search_ring = MagicMock(return_value=[])
        cortex.write_ring = MagicMock()
        cortex.twm_push = MagicMock()

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
    finally:
        os.utime(queue_path, (orig_atime, orig_mtime))


def test_check_response_never_raises():
    """Pass garbage to check_response and verify it returns gracefully."""
    from wild_igor.igor.cognition.action_claim_verifier import check_response

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
    from wild_igor.igor.cognition.action_claim_verifier import check_response

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
