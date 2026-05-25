"""
tests/test_after_action_reviewer.py — T-after-action-capture unit tests.

Tests _extract_cc_turn() and the deposit/skip logic without DB or Ollama.
"""

from unittest.mock import MagicMock, patch

# Note: after_action_reviewer imports cortex/ollama lazily inside try/except,
# so they fail silently without DB/Ollama in tests — no module-level stubs needed.

from devices.igor.tools.after_action_reviewer import _extract_cc_turn

# ── _extract_cc_turn ──────────────────────────────────────────────────────────


def _make_log_line(
    ts="2026-04-05T10:00:00",
    turn_id="turn-001",
    thread_id="cc:shared",
    tier="tier.2",
    elapsed="500ms",
    cost="$0.00",
    in_text="CC: what should we fix next",
    out_text="We should address the scope guard implementation first.",
):
    return f"{ts}|{turn_id}|{thread_id}|{tier}|{elapsed}|{cost}|IN:{in_text}|OUT:{out_text}"


def test_extract_cc_turn_basic():
    line = _make_log_line(
        in_text="CC: " + "x" * 35,
        out_text="A" * 60,
    )
    result = _extract_cc_turn(line)
    assert result is not None
    assert result["author"] == "claude-code"
    assert result["turn_id"] == "turn-001"


def test_extract_cc_turn_strips_cc_prefix():
    user_q = "design the scope guard for the PE chain"  # 40 chars > MIN_INPUT_LEN
    line = _make_log_line(in_text="CC: " + user_q, out_text="B" * 60)
    result = _extract_cc_turn(line)
    assert result is not None
    assert result["user_input"] == user_q


def test_extract_cc_turn_strips_routing_directive():
    line = _make_log_line(
        in_text="CC: fix the background routing bug in igor [Routing directive: use tier.2]",
        out_text="C" * 60,
    )
    result = _extract_cc_turn(line)
    assert result is not None
    assert "[Routing" not in result["user_input"]


def test_extract_cc_turn_skips_non_cc():
    line = _make_log_line(in_text="Hello Igor", out_text="D" * 60)
    assert _extract_cc_turn(line) is None


def test_extract_cc_turn_skips_short_input():
    line = _make_log_line(in_text="CC: hi", out_text="E" * 60)
    assert _extract_cc_turn(line) is None


def test_extract_cc_turn_skips_short_response():
    line = _make_log_line(in_text="CC: " + "x" * 35, out_text="short")
    assert _extract_cc_turn(line) is None


def test_extract_cc_turn_skips_malformed():
    assert _extract_cc_turn("not|enough|fields") is None
    assert _extract_cc_turn("") is None
    assert _extract_cc_turn("# comment line") is None


def test_extract_cc_turn_returns_all_fields():
    line = _make_log_line(
        ts="2026-04-05T12:00:00",
        turn_id="abc-123",
        in_text="CC: " + "what is the plan" * 3,
        out_text="The plan is to implement scope guard." * 2,
    )
    result = _extract_cc_turn(line)
    assert result is not None
    assert "ts" in result
    assert "response" in result
    assert len(result["response"]) > 0


# ── Registry smoke test ───────────────────────────────────────────────────────


def test_run_after_action_review_is_registered():
    """Verify the tool is registered in the registry."""
    from lab.utility_closet.registry import registry

    assert "run_after_action_review" in registry._tools
