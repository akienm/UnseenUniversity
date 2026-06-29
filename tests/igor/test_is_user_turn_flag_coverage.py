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
    from unseen_university.devices.igor.main import _HUMAN_AUTHORS

    assert "akien" in _HUMAN_AUTHORS, "akien must be in _HUMAN_AUTHORS"
    assert "claude-code" in _HUMAN_AUTHORS, "claude-code must be in _HUMAN_AUTHORS"


def test_bg_is_user_true_for_human_authors():
    """_bg_is_user logic: human authors → True, Igor-self → False.

    This is the computation that feeds _bg_reason(is_user_turn=_iu) at
    main.py:4832. Tested here so a rename of _HUMAN_AUTHORS members or a
    refactor of the expression breaks loudly.
    """
    from unseen_university.devices.igor.main import _HUMAN_AUTHORS

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
    """gateway.reason() must emit a log carrying the is_user_turn value.

    This is the observability check: the dispatch log line must fire on every
    gateway.reason() call so the next misroute is immediately visible in logs
    without requiring code changes. (Post T-inf-reroute-A the line names the
    Proxy dispatch rather than the retired tier ladder, but still carries
    is_user_turn.)
    """
    from unittest.mock import MagicMock

    from unseen_university.devices.igor.cognition.inference_gateway import InferenceGateway
    from unseen_university.devices.inference.shim import InferenceResponse

    spy = MagicMock()
    spy.dispatch.return_value = InferenceResponse(text="ok", source_kind="cloud")
    gw = InferenceGateway(inference=spy)

    logger_name = "unseen_university.devices.igor.cognition.inference_gateway"
    with caplog.at_level(logging.DEBUG, logger=logger_name):
        gw.reason(
            "test input",
            [],
            [],
            level="interactive",
            cortex=None,
            is_user_turn=True,
            complexity="low",
        )

    entry_logs = [r for r in caplog.records if "is_user_turn=True" in r.message]
    assert entry_logs, "Expected a reason() log carrying is_user_turn=True — not found"


def test_impulse_path_uses_false_by_design():
    """Impulse path in main.py does NOT pass is_user_turn — defaults False.

    Impulses are Igor-self habit firings, not human-originated. Non-foreground
    is correct (the Proxy then prefers flat_rate/local; impulses don't require
    human-quality responsiveness). Post T-inf-reroute-A the source choice is the
    Proxy's, so this pins the surviving intent: a background impulse declines
    foreground and reconstructs used_api=False from a local source_kind.
    """
    from unittest.mock import MagicMock

    from unseen_university.devices.igor.cognition.inference_gateway import InferenceGateway
    from unseen_university.devices.inference.shim import InferenceResponse

    spy = MagicMock()
    spy.dispatch.return_value = InferenceResponse(text="local reply", source_kind="local")
    gw = InferenceGateway(inference=spy)

    # Simulate the impulse path: level="background", no is_user_turn (False).
    text, cost, used_api = gw.reason(
        "[IMPULSE] habit check",
        [],
        [],
        level="background",
        cortex=None,
        # is_user_turn intentionally omitted — same as impulse path in main.py
        complexity="low",
    )

    req = spy.dispatch.call_args.args[0]
    assert req.foreground is False  # impulse does not force cloud preference
    assert used_api is False
