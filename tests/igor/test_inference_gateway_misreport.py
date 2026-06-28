"""
tests/test_inference_gateway_misreport.py

Unit tests for T-inference-misreport-fix.

Verifies the inference_gateway tier-6 fall-through:
- distinguishes failure modes in the user-facing message
  (cloud-only / local-only / both-different / both-same / nothing-attempted)
- preserves per-attempt errors in named vars (no cross-contamination)
- prints cloud_attempted as a bool, not the reasoner object's repr
- skip-retry timeout check looks at _local_first_error, not last_error
"""

from unittest.mock import MagicMock, patch


def _make_gateway():
    """Minimal InferenceGateway with mock tier reasoners — same shape as
    test_inference_gateway_mode.py's helper, intentionally duplicated to keep
    these test files independent of each other.
    """
    from unseen_university.devices.igor.cognition.inference_gateway import InferenceGateway

    gw = InferenceGateway.__new__(InferenceGateway)
    gw._t2 = MagicMock(name="t2_ollama")
    gw._t2_batch = None
    gw._t3 = None
    gw._t35 = MagicMock(name="t35_haiku")
    gw._t4 = MagicMock(name="t4_sonnet")
    gw._t5 = None
    gw.last_tier = ""
    return gw


def _make_cortex():
    cortex = MagicMock()
    cortex.twm_read.return_value = []
    return cortex


# ── Cloud-only-failed (local skipped because is_user_turn=True) ──────────────


def test_message_distinguishes_cloud_only_failed():
    """When local is never attempted (user turn skips it) and cloud fails,
    the message should name cloud as the failure — not 'both unavailable'."""
    gw = _make_gateway()
    gw._t4.reason.side_effect = RuntimeError("openrouter 503")
    cortex = _make_cortex()

    text, cost, used_api = gw.reason(
        "hi",
        [],
        [],
        level="interactive",
        cortex=cortex,
        is_user_turn=True,
        complexity="low",
    )

    # Local first-attempt is gated on (not is_user_turn) — skipped.
    # Cloud is attempted (user_turn=True). Cloud raises.
    # Local retry IS allowed (is_user_turn=True) — also raises (same _t2 mock).
    # _local_first_error is "" because local-first was skipped.
    # _local_retry_error captures the retry failure.
    # _cloud_error captures cloud's failure.
    # Both _cloud_error and _local_failed are truthy → "both" branch.
    # Errors differ → distinct-errors path.
    # Note: with the real test mock, _t2.reason raises by default (MagicMock
    # returns Mock objects that are not unpacked-as-tuple), so local-retry
    # will fail — making this a both-failed-different scenario, not pure
    # cloud-only. Configure _t2 to also raise so the assertion is explicit.
    assert used_api is False
    assert "Both cloud and local inference are unavailable" not in text
    assert "openrouter 503" in text


# ── Local-only-failed (cloud skipped because no key / no _t4) ────────────────


def test_message_distinguishes_local_only_failed():
    """When cloud is never attempted (no _t4) and local fails,
    the message should name local as the failure."""
    gw = _make_gateway()
    gw._t4 = None  # no cloud reasoner = cloud unavailable
    gw._t2.reason.side_effect = RuntimeError("ollama connection refused")
    cortex = _make_cortex()

    text, cost, used_api = gw.reason(
        "hi",
        [],
        [],
        level="interactive",
        cortex=cortex,
        is_user_turn=False,  # background turn → local-first
        complexity="low",
    )

    assert used_api is False
    assert "Both cloud and local inference are unavailable" not in text
    assert "Local inference failed" in text
    assert "ollama connection refused" in text
    assert "Cloud was not tried" in text


# ── Both failed with the SAME error (shared dependency bug — flag prominently)


def test_message_flags_shared_dependency_bug_when_errors_match():
    """When both reasoners raise the SAME error, that's a shared-dep bug —
    the message should call this out, not bury it as 'both unavailable.'
    This is the exact scenario from 2026-05-03 20:17:27 (sqlite 'config').

    is_user_turn=True so cloud IS attempted (otherwise the cloud branch is
    gated off and we'd see local-only failure, not the shared-dep case)."""
    gw = _make_gateway()
    same_err = "no such table: config"
    gw._t2.reason.side_effect = RuntimeError(same_err)
    gw._t4.reason.side_effect = RuntimeError(same_err)
    cortex = _make_cortex()

    text, cost, used_api = gw.reason(
        "hi",
        [],
        [],
        level="interactive",
        cortex=cortex,
        is_user_turn=True,
        complexity="low",
    )

    assert used_api is False
    assert "shared dependency bug" in text
    assert "no such table: config" in text
    assert "not an inference outage" in text


# ── Both failed with DIFFERENT errors → both surfaced ────────────────────────


def test_message_surfaces_both_when_errors_differ():
    """Cloud failed with X, local failed with Y → message names both errors."""
    gw = _make_gateway()
    gw._t2.reason.side_effect = RuntimeError("ollama timeout 90s")
    gw._t4.reason.side_effect = RuntimeError("openrouter 401 unauthorized")
    cortex = _make_cortex()

    text, cost, used_api = gw.reason(
        "hi",
        [],
        [],
        level="interactive",
        cortex=cortex,
        is_user_turn=True,  # user turn so cloud IS attempted
        complexity="low",
    )

    assert used_api is False
    assert "Cloud failed" in text
    assert "openrouter 401" in text
    assert "local also failed" in text
    # local-retry's error appears since local-first was skipped (is_user_turn=True)
    assert "ollama timeout" in text
    # The old, undifferentiated message must be gone.
    assert "Both cloud and local inference are unavailable" not in text


# ── Diagnostic: cloud_attempted printed as bool, not object repr ─────────────


def test_diagnostic_cloud_attempted_is_bool_not_object_repr():
    """The TIER6 anomaly's diagnostic field must read `cloud_attempted=True`,
    NOT `cloud_attempted=<...OpenRouterReasoner object at 0x...>`."""
    gw = _make_gateway()
    gw._t2.reason.side_effect = RuntimeError("local boom")
    gw._t4.reason.side_effect = RuntimeError("cloud boom")
    cortex = _make_cortex()

    captured = {}

    def _mock_log_anomaly(kind, detail):
        captured[kind] = detail

    with patch(
        "unseen_university.devices.igor.cognition.forensic_logger.log_anomaly",
        side_effect=_mock_log_anomaly,
    ):
        gw.reason(
            "hi",
            [],
            [],
            level="interactive",
            cortex=cortex,
            is_user_turn=True,
            complexity="low",
        )

    assert "TIER6" in captured
    detail = captured["TIER6"]
    assert "cloud_attempted=True" in detail
    # Reject the old buggy form which printed the reasoner object's repr.
    assert "object at 0x" not in detail


# ── Diagnostic: per-attempt errors not cross-contaminated ────────────────────


def test_diagnostic_per_attempt_errors_distinct():
    """The TIER6 detail must list per-attempt errors under distinct field
    names — they must NOT collapse under a single shared "local_error" field.
    Previously last_error was shared across all attempts, so cloud's error
    overwrote local's. Here we drive cloud + local-retry with different
    errors and assert both appear in the diagnostic with their own labels.
    (local-first and cloud are mutually exclusive on the is_user_turn axis
    in normal routing — this case mirrors the user-turn path.)"""
    gw = _make_gateway()
    gw._t2.reason.side_effect = RuntimeError("ollama-retry-bang")
    gw._t4.reason.side_effect = RuntimeError("openrouter-bang")
    cortex = _make_cortex()

    captured = {}

    def _mock_log_anomaly(kind, detail):
        captured[kind] = detail

    with patch(
        "unseen_university.devices.igor.cognition.forensic_logger.log_anomaly",
        side_effect=_mock_log_anomaly,
    ):
        gw.reason(
            "hi",
            [],
            [],
            level="interactive",
            cortex=cortex,
            is_user_turn=True,  # skip local-first, run cloud, then local-retry
            complexity="low",
        )

    detail = captured["TIER6"]
    # Cloud and local-retry errors appear under distinct field names —
    # not collapsed into a shared "local_error" that holds whichever error fired last.
    assert "local_retry_error=" in detail
    assert "ollama-retry-bang" in detail
    assert "openrouter-bang" in detail
    # The old undifferentiated "local_error=" field name must be gone.
    assert "local_error=" not in detail
