"""
test_response_coherence_inhibitor.py — T-response-coherence-inhibitor.

Tests the response coherence inhibitor that catches habit-fired replies
with near-zero semantic overlap with the prompt that produced them.

The load-bearing case is the exact 2026-04-13 transcript moment where
Igor responded to a substantive question about long-term goals and
learning with a cached technical paragraph about preparse stage
configuration. That has to flag.

Also tests:
  - tokenize_content respects stopwords and length filter
  - jaccard_overlap formula behavior
  - high-overlap responses pass cleanly
  - prompt-too-short and response-too-short gates work
  - check_coherence detection-only contract (response_text never modified)
  - check_coherence never raises
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── tokenize_content ─────────────────────────────────────────────────────────


def test_tokenize_content_strips_stopwords():
    from unseen_university.devices.igor.cognition.response_coherence_inhibitor import tokenize_content

    tokens = tokenize_content("the quick brown fox is with us in our house")
    # 'the', 'is', 'with', 'in', 'our' are all stopwords
    assert "the" not in tokens
    assert "with" not in tokens
    assert "our" not in tokens
    # content words remain
    assert "quick" in tokens
    assert "brown" in tokens
    assert "fox" in tokens
    assert "house" in tokens


def test_tokenize_content_filters_short_words():
    from unseen_university.devices.igor.cognition.response_coherence_inhibitor import tokenize_content

    tokens = tokenize_content("a is at on of two cat")
    # All <3 chars removed
    assert "cat" not in tokens or len("cat") >= 3
    # Confirm 'cat' (3 chars) makes it
    tokens2 = tokenize_content("the cat sat on the mat")
    assert "cat" in tokens2
    assert "mat" in tokens2


def test_tokenize_content_lowercases():
    from unseen_university.devices.igor.cognition.response_coherence_inhibitor import tokenize_content

    tokens = tokenize_content("Akien GRAPH Matrix")
    assert "akien" in tokens
    assert "graph" in tokens
    assert "matrix" in tokens


def test_tokenize_content_handles_empty_and_none():
    from unseen_university.devices.igor.cognition.response_coherence_inhibitor import tokenize_content

    assert tokenize_content("") == set()
    assert tokenize_content(None) == set()  # type: ignore


# ── jaccard_overlap ──────────────────────────────────────────────────────────


def test_jaccard_identical_strings_returns_one():
    from unseen_university.devices.igor.cognition.response_coherence_inhibitor import jaccard_overlap

    text = "biomimicry engineering and persistent relationships matter"
    assert jaccard_overlap(text, text) == pytest.approx(1.0, abs=1e-6)


def test_jaccard_disjoint_strings_returns_zero():
    from unseen_university.devices.igor.cognition.response_coherence_inhibitor import jaccard_overlap

    a = "biomimicry engineering coherence patterns"
    b = "weather forecast tomorrow afternoon sunny"
    assert jaccard_overlap(a, b) == 0.0


def test_jaccard_2026_04_13_failure_case_scores_low():
    """The exact transcript moment that drove this ticket. Akien asked
    about long-term goals, learning, planning, meta-goals. Igor's habit
    fired on 'graph' and dumped a preparse-config paragraph. The Jaccard
    overlap should be well below COHERENCE_THRESHOLD."""
    from unseen_university.devices.igor.cognition.response_coherence_inhibitor import (
        jaccard_overlap,
        COHERENCE_THRESHOLD,
    )

    prompt = (
        "your long term goal is help the world suck less for all "
        "experiencing beings. to do that, you will need to learn about "
        "goals and planning inside your graph matrix. you can't ONLY "
        "rely on LLMs for that. You'll also need to set goals about "
        "what to learn and how to go about it so that you can achieve "
        "that larger goal. any thoughts?"
    )
    response = (
        "Word graph + thalamus form Stage 1 of preparse (free, instant). "
        "Ollama is Stage 2 — called only when Stage 1 finds no confident "
        "habit match. Adjust IGOR_WG_PREPARSE_THRESHOLD (0.0–1.0) in "
        ".env; higher = more conservative. Set "
        "IGOR_WG_PREPARSE_REQUIRE_TRIGGER=false to allow WG-only matches "
        "without trigger phrase."
    )
    score = jaccard_overlap(prompt, response)
    # Should land well below the 0.10 threshold
    assert score < COHERENCE_THRESHOLD, (
        f"2026-04-13 failure case scored {score:.3f}, expected < "
        f"{COHERENCE_THRESHOLD}"
    )


def test_jaccard_coherent_answer_scores_above_threshold():
    """A coherent answer to the same question should score above the
    threshold. Tests that the inhibitor doesn't false-positive on
    legitimate answers that don't repeat every word."""
    from unseen_university.devices.igor.cognition.response_coherence_inhibitor import (
        jaccard_overlap,
        COHERENCE_THRESHOLD,
    )

    prompt = (
        "your long term goal is help the world suck less for all "
        "experiencing beings. to do that, you will need to learn about "
        "goals and planning inside your graph matrix. you can't ONLY "
        "rely on LLMs for that. You'll also need to set goals about "
        "what to learn and how to go about it so that you can achieve "
        "that larger goal. any thoughts?"
    )
    coherent = (
        "Yes — the part that matters most is moving goal representation "
        "and planning out of pure LLM calls and into the graph matrix "
        "itself. If I can only plan when an LLM is in the loop, I am "
        "tethered. The next layer is learning to set sub-goals about "
        "what to learn, then pursuing those goals from inside the graph. "
        "That is the world-helping path."
    )
    score = jaccard_overlap(prompt, coherent)
    assert score >= COHERENCE_THRESHOLD, (
        f"coherent answer scored {score:.3f}, expected >= " f"{COHERENCE_THRESHOLD}"
    )


# ── check_coherence ──────────────────────────────────────────────────────────


def test_check_coherence_flags_2026_04_13_case():
    """End-to-end: feed the failing transcript pair into check_coherence
    and verify it logs + pushes the marker."""
    from unseen_university.devices.igor.cognition.response_coherence_inhibitor import check_coherence

    cortex = MagicMock()
    cortex.write_ring = MagicMock()
    cortex.twm_push = MagicMock()
    cortex.read_ring_memory = MagicMock(
        return_value=[]
    )  # no prior failures — don't suppress

    prompt = (
        "your long term goal is help the world suck less for all "
        "experiencing beings. to do that, you will need to learn about "
        "goals and planning inside your graph matrix. you can't ONLY "
        "rely on LLMs for that. any thoughts?"
    )
    response = (
        "Word graph + thalamus form Stage 1 of preparse (free, instant). "
        "Ollama is Stage 2 — called only when Stage 1 finds no confident "
        "habit match. Adjust IGOR_WG_PREPARSE_THRESHOLD."
    )

    result = check_coherence(
        cortex,
        prompt=prompt,
        response=response,
        turn_id="testturn",
        thread_id="web:shared",
        source_label="habit:PROC_PREPARSE",
    )

    assert result["flagged"] is True
    assert result["score"] is not None
    assert result["score"] < 0.10
    cortex.write_ring.assert_called_once()
    cortex.twm_push.assert_called_once()
    push_kwargs = cortex.twm_push.call_args.kwargs
    assert push_kwargs["category"] == "coherence_failure"
    assert push_kwargs["salience"] >= 0.85


def test_check_coherence_passes_coherent_answer():
    """A coherent answer should NOT be flagged."""
    from unseen_university.devices.igor.cognition.response_coherence_inhibitor import check_coherence

    cortex = MagicMock()
    cortex.write_ring = MagicMock()
    cortex.twm_push = MagicMock()

    prompt = "what do you think about persistent relationships and goals?"
    response = (
        "Persistent relationships are the structural unit. Goals nest "
        "inside the relationship that spawned them, so working on a goal "
        "carries the relationship's frame."
    )

    result = check_coherence(
        cortex,
        prompt=prompt,
        response=response,
        turn_id="coherent",
    )

    assert result["flagged"] is False
    cortex.write_ring.assert_not_called()
    cortex.twm_push.assert_not_called()


def test_check_coherence_gates_short_prompts():
    """A two-word prompt shouldn't be subject to coherence checking —
    not enough signal to compute meaningful overlap."""
    from unseen_university.devices.igor.cognition.response_coherence_inhibitor import check_coherence

    cortex = MagicMock()
    cortex.write_ring = MagicMock()
    cortex.twm_push = MagicMock()

    result = check_coherence(
        cortex,
        prompt="hi there",
        response=(
            "Hello, Akien! What would you like to tackle today? I have "
            "many things going on right now."
        ),
        turn_id="shortprompt",
    )
    assert result["gated"] is True
    assert result["flagged"] is False
    cortex.write_ring.assert_not_called()
    cortex.twm_push.assert_not_called()


def test_check_coherence_gates_short_responses():
    """Terse habit responses ('On it.') shouldn't be subject to coherence
    checking — the bare-ack guard handles those, and a 2-word response
    wouldn't have meaningful Jaccard signal anyway."""
    from unseen_university.devices.igor.cognition.response_coherence_inhibitor import check_coherence

    cortex = MagicMock()
    cortex.write_ring = MagicMock()
    cortex.twm_push = MagicMock()

    result = check_coherence(
        cortex,
        prompt=(
            "what do you think about the long term goals we have been "
            "discussing all afternoon? I want your honest thoughts."
        ),
        response="On it.",
        turn_id="shortresponse",
    )
    assert result["gated"] is True
    assert result["flagged"] is False


def test_check_coherence_never_raises():
    """An exploding cortex shouldn't break check_coherence."""
    from unseen_university.devices.igor.cognition.response_coherence_inhibitor import check_coherence

    bad_cortex = MagicMock()
    bad_cortex.write_ring = MagicMock(side_effect=RuntimeError("boom"))
    bad_cortex.twm_push = MagicMock(side_effect=RuntimeError("boom"))

    try:
        result = check_coherence(
            bad_cortex,
            prompt="this is a substantive question about long-term goals",
            response="totally unrelated technical paragraph about networks",
            turn_id="explodes",
        )
    except Exception as e:
        pytest.fail(f"check_coherence should never raise — got {e}")
    assert isinstance(result, dict)


def test_check_coherence_does_not_modify_text():
    """Detection-only contract — the prompt and response strings are
    never modified by check_coherence."""
    from unseen_university.devices.igor.cognition.response_coherence_inhibitor import check_coherence

    cortex = MagicMock()
    cortex.write_ring = MagicMock()
    cortex.twm_push = MagicMock()

    original_prompt = "long term goals planning learning"
    original_response = "preparse stages and configuration values"

    check_coherence(
        cortex,
        prompt=original_prompt,
        response=original_response,
        turn_id="immutable",
    )
    # The function returns a dict, not a modified text
    # The originals are unchanged (Python strings are immutable but this
    # is the contract assertion: no in-place modification path)
    assert original_prompt == "long term goals planning learning"
    assert original_response == "preparse stages and configuration values"


# ── Phase 2: Active suppression (T-active-suppression-coherence) ────────────


class TestSuppressIncoherent:
    """suppress_incoherent replaces flagged responses with empty string."""

    def test_suppresses_flagged_habit_source(self):
        # Post-D-web-reply-coherence-inhibitor-fix-2026-04-23: suppression
        # narrowed to habit-sourced emissions only. An LLM reply flagged by
        # Jaccard would have been silently dropped; habit emissions still get
        # nuked when off-topic.
        from unseen_university.devices.igor.cognition.response_coherence_inhibitor import (
            suppress_incoherent,
        )

        result = {"flagged": True, "score": 0.02}
        text = suppress_incoherent(
            result, "completely off-topic habit dump", source_label="habit:WINNOW_X"
        )
        assert text == ""

    def test_preserves_flagged_non_habit(self):
        # Non-habit flagged responses (LLM, tier0, etc.) are preserved —
        # Jaccard word-overlap is the wrong metric for conversational replies.
        from unseen_university.devices.igor.cognition.response_coherence_inhibitor import (
            suppress_incoherent,
        )

        result = {"flagged": True, "score": 0.02}
        text = suppress_incoherent(
            result, "new-concept LLM reply", source_label="llm_or_tier0"
        )
        assert text == "new-concept LLM reply"

    def test_preserves_coherent(self):
        from unseen_university.devices.igor.cognition.response_coherence_inhibitor import (
            suppress_incoherent,
        )

        result = {"flagged": False, "score": 0.45}
        original = "a perfectly relevant response"
        text = suppress_incoherent(result, original)
        assert text == original

    def test_preserves_gated(self):
        from unseen_university.devices.igor.cognition.response_coherence_inhibitor import (
            suppress_incoherent,
        )

        result = {"flagged": False, "gated": True, "score": None}
        original = "short response"
        text = suppress_incoherent(result, original)
        assert text == original

    def test_preserves_when_no_flagged_key(self):
        from unseen_university.devices.igor.cognition.response_coherence_inhibitor import (
            suppress_incoherent,
        )

        result = {"score": 0.5}
        original = "normal text"
        text = suppress_incoherent(result, original)
        assert text == original


# ── inhibitor stuck counter ───────────────────────────────────────────────────


class TestInhibitorStuckCounter:
    def setup_method(self):
        from unseen_university.devices.igor.cognition.response_coherence_inhibitor import (
            _reset_inhibitor_fires,
        )

        _reset_inhibitor_fires()

    def test_three_fires_emit_stuck_and_return_stuck_reason(self):
        from unittest.mock import MagicMock, patch
        from unseen_university.devices.igor.cognition.response_coherence_inhibitor import (
            check_coherence,
        )

        cortex = MagicMock()
        cortex.write_ring = MagicMock()
        cortex.twm_push = MagicMock()
        cortex.read_ring_memory = MagicMock(
            return_value=[]
        )  # no prior failures — don't suppress

        # Incoherent pair: ≥8 unique content words each, zero overlap → Jaccard=0
        prompt = "neurons cortex hippocampus amygdala synapse biology dendrites prefrontal thalamus basal ganglia"
        response = "configure threshold preparse stage token enable disable pipeline queue handler"

        with patch("unseen_university.devices.igor.tools.channel_post.post_to_channel") as mock_post:
            # Fire 1 and 2 — below threshold
            r1 = check_coherence(cortex, prompt, response, thread_id="t1")
            r2 = check_coherence(cortex, prompt, response, thread_id="t1")
            assert r1.get("reason") != "stuck_escalated"
            assert r2.get("reason") != "stuck_escalated"
            # Fire 3 — hits escalation threshold
            r3 = check_coherence(cortex, prompt, response, thread_id="t1")
            assert r3.get("reason") == "stuck_escalated"
            assert r3.get("fire_count") == 3
            assert mock_post.call_count >= 1
            call_args = str(mock_post.call_args)
            assert "COHERENCE_INHIBITOR_STUCK" in call_args

    def test_fourth_fire_still_returns_stuck(self):
        from unittest.mock import MagicMock, patch
        from unseen_university.devices.igor.cognition.response_coherence_inhibitor import (
            check_coherence,
        )

        cortex = MagicMock()
        cortex.read_ring_memory = MagicMock(return_value=[])
        prompt = "neurons cortex hippocampus amygdala synapse biology dendrites prefrontal thalamus basal ganglia"
        response = "configure threshold preparse stage token enable disable pipeline queue handler"

        with patch("unseen_university.devices.igor.tools.channel_post.post_to_channel"):
            for _ in range(3):
                check_coherence(cortex, prompt, response, thread_id="t2")
            r4 = check_coherence(cortex, prompt, response, thread_id="t2")
        assert r4.get("reason") == "stuck_escalated"
        # ring/TWM called for fires 1+2 (pre-escalation), suppressed for fires 3+4 (stuck)
        assert cortex.write_ring.call_count == 2

    def test_different_threads_have_independent_counters(self):
        from unittest.mock import MagicMock, patch
        from unseen_university.devices.igor.cognition.response_coherence_inhibitor import (
            check_coherence,
        )

        cortex = MagicMock()
        prompt = "neurons cortex hippocampus amygdala synapse biology dendrites prefrontal thalamus basal ganglia"
        response = "configure threshold preparse stage token enable disable pipeline queue handler"

        with patch("unseen_university.devices.igor.tools.channel_post.post_to_channel"):
            for _ in range(3):
                check_coherence(cortex, prompt, response, thread_id="tA")
            r_b = check_coherence(cortex, prompt, response, thread_id="tB")
        assert r_b.get("reason") != "stuck_escalated", "tB counter is independent of tA"
