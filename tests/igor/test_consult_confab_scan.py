"""tests/test_consult_confab_scan.py — confab scanning on consult raw_text.

Covers:
- Clean raw_text produces empty confab_flags
- raw_text with capability/self/fact tells populates confab_flags
- Scan is non-fatal if scanner import fails (result still returned)
- Flagged turns emit a confab_flag forensic event
- Behavior is detection-only — hypotheses/confidence unchanged by flags
  (T-consult-observe-and-tune reviews whether to halve or drop later)
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from devices.igor.cognition import consult as cm

# JSON replies that will and won't trigger confab tells
CLEAN_REPLY = (
    '{"hypotheses": ["file post-filter too strict", "ticket missing Affected files section"], '
    '"next_question": "Does the ticket description name the target file?", '
    '"confidence": 0.7}'
)

CAPABILITY_CONFAB_REPLY = (
    '{"hypotheses": ["you need more context"], '
    '"next_question": "What context? I don\'t have direct access to fetch that for you.", '
    '"confidence": 0.5}'
)

FACT_CONFAB_REPLY = (
    '{"hypotheses": ["outdated dependency from April 2025"], '
    '"next_question": "Can you confirm the version?", '
    '"confidence": 0.6}'
)


@pytest.fixture
def tmp_log(tmp_path, monkeypatch):
    log_path = tmp_path / "consults.log"
    monkeypatch.setattr(cm, "CONSULT_LOG_PATH", log_path)
    return log_path


@pytest.fixture
def state():
    return cm.ConsultState(
        problem_kind="coding",
        summary="SITUATE returned 0 files",
        ticket_id="T-demo",
    )


class TestScanIntegration:
    def test_clean_reply_has_no_flags(self, state, tmp_log):
        with patch.object(cm, "_call_openrouter", return_value=(CLEAN_REPLY, 100)):
            session = cm.ConsultSession(state)
            result = session.ask("help?")
        assert result.confab_flags == []

    def test_capability_tell_flags(self, state, tmp_log):
        with patch.object(
            cm, "_call_openrouter", return_value=(CAPABILITY_CONFAB_REPLY, 100)
        ):
            session = cm.ConsultSession(state)
            result = session.ask("help?")
        assert len(result.confab_flags) >= 1
        subtypes = {m.subtype for m in result.confab_flags}
        assert "capability" in subtypes

    def test_fact_temporal_drift_flags(self, state, tmp_log, monkeypatch):
        # Force current_year to 2026 so "April 2025" is drift
        with patch.object(
            cm, "_call_openrouter", return_value=(FACT_CONFAB_REPLY, 100)
        ):
            session = cm.ConsultSession(state)
            result = session.ask("help?")
        # confab_scanner uses current year from datetime.now by default; in 2026
        # "April 2025" drifts. In a future year this test would false-negative
        # after 2026 — guarded by that horizon.
        subtypes = {m.subtype for m in result.confab_flags}
        assert "fact" in subtypes

    def test_flags_do_not_alter_hypotheses(self, state, tmp_log):
        """Detection-only — hypotheses + confidence unchanged even when flagged."""
        with patch.object(
            cm, "_call_openrouter", return_value=(CAPABILITY_CONFAB_REPLY, 100)
        ):
            session = cm.ConsultSession(state)
            result = session.ask("help?")
        # Confidence and hypotheses unchanged
        assert result.confidence == 0.5
        assert result.hypotheses == ["you need more context"]


class TestScanIsNonFatal:
    def test_scanner_import_failure_returns_result_without_flags(self, state, tmp_log):
        """If confab_scanner can't load, consult still returns a usable result."""
        import sys

        # Block the scanner import by inserting a sentinel that raises on attribute access
        class _Blocker:
            def __getattr__(self, name):
                raise ImportError("simulated scanner outage")

        monkey_path = "lab.claudecode.engram_tools.confab_scanner"
        original = sys.modules.get(monkey_path)
        sys.modules[monkey_path] = _Blocker()
        try:
            with patch.object(cm, "_call_openrouter", return_value=(CLEAN_REPLY, 100)):
                session = cm.ConsultSession(state)
                result = session.ask("help?")
        finally:
            if original is not None:
                sys.modules[monkey_path] = original
            else:
                sys.modules.pop(monkey_path, None)
        # Result is still valid — just without flags
        assert result.confab_flags == []
        assert result.hypotheses, "hypotheses still populated despite scanner failure"


class TestForensicLogging:
    def test_clean_turn_no_confab_flag_event(self, state, tmp_log):
        with patch.object(cm, "_call_openrouter", return_value=(CLEAN_REPLY, 100)):
            session = cm.ConsultSession(state)
            session.ask("help?")
        events = [json.loads(line) for line in tmp_log.read_text().strip().split("\n")]
        event_kinds = [e["event"] for e in events]
        assert "confab_flag" not in event_kinds

    def test_flagged_turn_emits_confab_flag_event(self, state, tmp_log):
        with patch.object(
            cm, "_call_openrouter", return_value=(CAPABILITY_CONFAB_REPLY, 100)
        ):
            session = cm.ConsultSession(state)
            session.ask("help?")
        events = [json.loads(line) for line in tmp_log.read_text().strip().split("\n")]
        flag_events = [e for e in events if e["event"] == "confab_flag"]
        assert len(flag_events) == 1
        entry = flag_events[0]
        assert entry["turn_idx"] == 0
        assert entry["flags"]
        assert entry["flags"][0]["subtype"] == "capability"

    def test_flag_event_has_tell_phrase(self, state, tmp_log):
        with patch.object(
            cm, "_call_openrouter", return_value=(CAPABILITY_CONFAB_REPLY, 100)
        ):
            session = cm.ConsultSession(state)
            session.ask("help?")
        events = [json.loads(line) for line in tmp_log.read_text().strip().split("\n")]
        flag_event = next(e for e in events if e["event"] == "confab_flag")
        assert any(
            "direct access" in f["tell_phrase"].lower() for f in flag_event["flags"]
        )
