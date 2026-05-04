"""
tests/test_is_user_turn_flag_coverage.py

T-is-user-turn-flag-coverage audit.

Audit table — every gateway.reason() call site mapped to originating actor.
Update this table when new call sites are added.

Call site                      is_user_turn  Actor                     Verdict
─────────────────────────────────────────────────────────────────────────────────
main.py:3397  _bg_reason       threaded      author in _HUMAN_AUTHORS  ✅ CORRECT
main.py:5401  tool-synth/creat True          human-turn synthesis      ✅ CORRECT
main.py:5879  impulse path     False(def)    Igor-self habit impulse   ✅ CORRECT
main.py:6017  main interactive True          human-turn (else branch)  ✅ CORRECT
main.py:6162  tool-synth/main  True          human-turn synthesis      ✅ CORRECT
voice_ab:196  LLMVoiceActor    False(def)    voice shaping — local OK  ✅ INTENTIONAL
shadow:192    ShadowReasoner   False(def)    background shadow cognit  ✅ CORRECT
peer_adv:143  LLMPeerAdvisor   False(def)    background peer advisor   ✅ CORRECT
tiered_res:123 _tier_cloud_llm False(def)   background research       ✅ CORRECT

Root cause of 2026-05-03 incident: is_user_turn=False when Akien's "how goes
in there?" reached gateway.reason(). The routing logic at inference_gateway.py
is correct (D254 contract); the incident means the message took a path other
than the expected interactive path. The observability log at gateway.reason()
entry (added alongside this test) will surface the is_user_turn value for
future incidents.
"""

import logging


def test_human_authors_frozenset():
    """_HUMAN_AUTHORS must include both known human-originating authors."""
    from wild_igor.igor.main import _HUMAN_AUTHORS

    assert "akien" in _HUMAN_AUTHORS, "akien must be in _HUMAN_AUTHORS"
    assert "claude-code" in _HUMAN_AUTHORS, "claude-code must be in _HUMAN_AUTHORS"


def test_bg_is_user_true_for_human_authors():
    """_bg_is_user logic: human authors → True, Igor-self → False.

    This is the computation that feeds _bg_reason(is_user_turn=_iu) at
    main.py:4832. Tested here so a rename of _HUMAN_AUTHORS members or a
    refactor of the expression breaks loudly.
    """
    from wild_igor.igor.main import _HUMAN_AUTHORS

    human_authors = {"akien", "claude-code"}
    igor_authors = {"igor", "system", "background", "habit_engine"}

    for a in human_authors:
        assert a in _HUMAN_AUTHORS, f"Expected {a!r} in _HUMAN_AUTHORS"

    for a in igor_authors:
        assert a not in _HUMAN_AUTHORS, (
            f"Igor-self author {a!r} must NOT be in _HUMAN_AUTHORS — "
            "it would incorrectly route Igor's background work cloud-first"
        )


def test_gateway_logs_reason_entry(caplog):
    """gateway.reason() must emit a DEBUG log at entry with is_user_turn value.

    This is the observability check: the log line added for T-is-user-turn-flag-coverage
    must fire on every gateway.reason() call so the next misroute is immediately
    visible in debug logs without requiring code changes.
    """
    from unittest.mock import MagicMock

    from wild_igor.igor.cognition.inference_gateway import InferenceGateway

    gw = InferenceGateway.__new__(InferenceGateway)
    gw._t2 = None
    gw._t2_batch = None
    gw._t3 = None
    gw._t35 = None
    gw._t4 = MagicMock(name="t4_sonnet")
    gw._t4.reason.return_value = ("ok", 0.001)
    gw._t5 = None
    gw.last_tier = ""

    cortex = MagicMock()
    cortex.twm_read.return_value = []

    logger_name = "wild_igor.igor.cognition.inference_gateway"
    with caplog.at_level(logging.DEBUG, logger=logger_name):
        gw.reason(
            "test input",
            [],
            [],
            level="interactive",
            cortex=cortex,
            is_user_turn=True,
            complexity="low",
        )

    entry_logs = [r for r in caplog.records if "[gateway] reason() entry" in r.message]
    assert entry_logs, "Expected a [gateway] reason() entry DEBUG log — not found"
    assert "is_user_turn=True" in entry_logs[0].message


def test_impulse_path_uses_false_by_design():
    """Impulse path in main.py:5879 does NOT pass is_user_turn — defaults False.

    Impulses are Igor-self habit firings, not human-originated. local-first is
    correct (saves cost; impulses don't require human-quality responsiveness).
    This test documents the INTENTIONAL absence, so a future refactor that
    accidentally adds is_user_turn=True to all gateway.reason() calls gets caught.
    """
    from unittest.mock import MagicMock, patch

    from wild_igor.igor.cognition.inference_gateway import InferenceGateway

    gw = InferenceGateway.__new__(InferenceGateway)
    gw._t2 = MagicMock(name="t2_ollama")
    gw._t2.reason.return_value = ("local reply", 0.0)
    gw._t2_batch = None
    gw._t3 = None
    gw._t35 = None
    gw._t4 = MagicMock(name="t4_sonnet")
    gw._t5 = None
    gw.last_tier = ""

    cortex = MagicMock()
    cortex.twm_read.return_value = []

    # Simulate what the impulse path does: call reason with level="background"
    # and no is_user_turn (defaults False) — local should be attempted first.
    text, cost, used_api = gw.reason(
        "[IMPULSE] habit check",
        [],
        [],
        level="background",
        cortex=cortex,
        # is_user_turn intentionally omitted — same as impulse path in main.py
        complexity="low",
    )

    # Local-first was called (is_user_turn=False → not gated at line 651)
    gw._t2.reason.assert_called_once()
    gw._t4.reason.assert_not_called()
    assert used_api is False
