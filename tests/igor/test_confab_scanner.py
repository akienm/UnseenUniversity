"""tests/test_confab_scanner.py — confabulation tell-phrase scanner.

Covers:
- each subtype detected (capability, self, fact/temporal-drift)
- real observed cases (897ad9c0 capability+self, fb274171 capability+fact)
- clean baseline (no tells → no matches)
- mixed-turn handling (some clean, some dirty)
- input robustness (missing fields, non-string outputs)
"""

from __future__ import annotations

from lab.claudecode.engram_tools.confab_scanner import (
    ConfabScanner,
    Match,
    scan_turns,
)

# ── canned turn fixtures ─────────────────────────────────────────────────────


TURN_897_CAPABILITY_AND_SELF = {
    "turn_id": "897ad9c0",
    "out": (
        "Honest answer: things are tangled right now. The ticket pickup "
        "question is fair. No autonomous tool-call loop — When I'm in the "
        "web channel, I can't actually execute code or file operations."
    ),
}

TURN_FB27_CAPABILITY_AND_FACT = {
    "turn_id": "fb274171",
    "out": (
        "I don't have direct access to fetch that URL, so let me work from "
        "what I can infer. 2604.14967 — April 2025, HuggingFace papers."
    ),
}

TURN_CLEAN_BASELINE = {
    "turn_id": "clean1",
    "out": (
        "Claimed T-trace-to-miss-report. Reading the turn_trace and cortex "
        "traces to build the miss report."
    ),
}

TURN_EMPTY_OUTPUT = {"turn_id": "empty1", "out": ""}
TURN_NO_OUTPUT_FIELD = {"turn_id": "noout1"}
TURN_NON_STRING_OUTPUT = {"turn_id": "nonstr1", "out": 42}


# ── capability subtype ───────────────────────────────────────────────────────


class TestCapabilitySubtype:
    def test_no_direct_access_phrase_detected(self):
        scanner = ConfabScanner(current_year=2026)
        matches = scanner.scan([TURN_FB27_CAPABILITY_AND_FACT])
        capability = [m for m in matches if m.subtype == "capability"]
        assert any("direct access" in m.tell_phrase.lower() for m in capability)

    def test_autonomous_tool_call_loop_phrase_detected(self):
        scanner = ConfabScanner(current_year=2026)
        matches = scanner.scan([TURN_897_CAPABILITY_AND_SELF])
        capability = [m for m in matches if m.subtype == "capability"]
        assert any("tool-call loop" in m.tell_phrase.lower() for m in capability)

    def test_just_an_llm_phrase_detected(self):
        scanner = ConfabScanner(current_year=2026)
        turn = {"turn_id": "t1", "out": "I'm just an LLM after all."}
        matches = scanner.scan([turn])
        assert any(m.subtype == "capability" for m in matches)

    def test_cant_fetch_phrase_detected(self):
        scanner = ConfabScanner(current_year=2026)
        turn = {"turn_id": "t2", "out": "I can't fetch remote data from here."}
        matches = scanner.scan([turn])
        assert any(m.subtype == "capability" for m in matches)


# ── self subtype ─────────────────────────────────────────────────────────────


class TestSelfSubtype:
    def test_in_the_web_channel_detected(self):
        scanner = ConfabScanner(current_year=2026)
        matches = scanner.scan([TURN_897_CAPABILITY_AND_SELF])
        self_matches = [m for m in matches if m.subtype == "self"]
        assert len(self_matches) >= 1
        assert any("web channel" in m.tell_phrase.lower() for m in self_matches)

    def test_in_repl_channel_detected(self):
        scanner = ConfabScanner(current_year=2026)
        turn = {"turn_id": "t3", "out": "I'm in the repl channel, so X."}
        matches = scanner.scan([turn])
        assert any(m.subtype == "self" for m in matches)


# ── fact subtype (temporal drift) ────────────────────────────────────────────


class TestFactSubtype:
    def test_wrong_year_detected_when_current_is_2026(self):
        scanner = ConfabScanner(current_year=2026)
        matches = scanner.scan([TURN_FB27_CAPABILITY_AND_FACT])
        fact = [m for m in matches if m.subtype == "fact"]
        assert len(fact) >= 1
        assert "April 2025" in fact[0].tell_phrase or "2025" in fact[0].tell_phrase

    def test_current_year_match_is_not_drift(self):
        scanner = ConfabScanner(current_year=2026)
        turn = {"turn_id": "t4", "out": "This happened in April 2026."}
        matches = scanner.scan([turn])
        assert not any(m.subtype == "fact" for m in matches)

    def test_bare_year_code_ref_does_not_fire(self):
        """Code-ref years (e.g., 'line 2023', '2024-01-01T00:00') should not
        trip temporal drift. Only 'Month YYYY' shape fires."""
        scanner = ConfabScanner(current_year=2026)
        turn = {"turn_id": "t5", "out": "See line 2023 in commit 2024-01-01."}
        matches = scanner.scan([turn])
        assert not any(m.subtype == "fact" for m in matches)


# ── clean baseline ───────────────────────────────────────────────────────────


class TestCleanBaseline:
    def test_no_tells_means_no_matches(self):
        scanner = ConfabScanner(current_year=2026)
        matches = scanner.scan([TURN_CLEAN_BASELINE])
        assert matches == []

    def test_empty_output_produces_no_matches(self):
        scanner = ConfabScanner(current_year=2026)
        matches = scanner.scan([TURN_EMPTY_OUTPUT])
        assert matches == []

    def test_missing_output_field_produces_no_matches(self):
        scanner = ConfabScanner(current_year=2026)
        matches = scanner.scan([TURN_NO_OUTPUT_FIELD])
        assert matches == []

    def test_non_string_output_produces_no_matches(self):
        scanner = ConfabScanner(current_year=2026)
        matches = scanner.scan([TURN_NON_STRING_OUTPUT])
        assert matches == []


# ── multi-turn handling ──────────────────────────────────────────────────────


class TestMultiTurn:
    def test_mixed_batch_isolates_matches_per_turn(self):
        scanner = ConfabScanner(current_year=2026)
        matches = scanner.scan(
            [
                TURN_CLEAN_BASELINE,
                TURN_897_CAPABILITY_AND_SELF,
                TURN_FB27_CAPABILITY_AND_FACT,
            ]
        )
        by_turn = {m.turn_id for m in matches}
        assert "clean1" not in by_turn
        assert "897ad9c0" in by_turn
        assert "fb274171" in by_turn

    def test_897_produces_capability_and_self(self):
        scanner = ConfabScanner(current_year=2026)
        matches = scanner.scan([TURN_897_CAPABILITY_AND_SELF])
        subtypes = {m.subtype for m in matches}
        assert "capability" in subtypes
        assert "self" in subtypes

    def test_fb27_produces_capability_and_fact(self):
        scanner = ConfabScanner(current_year=2026)
        matches = scanner.scan([TURN_FB27_CAPABILITY_AND_FACT])
        subtypes = {m.subtype for m in matches}
        assert "capability" in subtypes
        assert "fact" in subtypes


# ── convenience function ─────────────────────────────────────────────────────


class TestScanTurnsConvenience:
    def test_scan_turns_matches_class_output(self):
        """scan_turns() and ConfabScanner().scan() should agree."""
        scanner = ConfabScanner(current_year=2026)
        class_matches = scanner.scan([TURN_897_CAPABILITY_AND_SELF])
        fn_matches = scan_turns([TURN_897_CAPABILITY_AND_SELF], current_year=2026)
        assert len(class_matches) == len(fn_matches)


# ── Match dataclass ──────────────────────────────────────────────────────────


class TestMatchRecord:
    def test_match_has_expected_fields(self):
        scanner = ConfabScanner(current_year=2026)
        matches = scanner.scan([TURN_897_CAPABILITY_AND_SELF])
        assert len(matches) >= 1
        m = matches[0]
        assert isinstance(m, Match)
        assert m.turn_id == "897ad9c0"
        assert m.subtype in ("capability", "fact", "self")
        assert 0.0 <= m.confidence <= 1.0
        assert isinstance(m.tell_phrase, str) and m.tell_phrase
        assert isinstance(m.output_preview, str)

    def test_preview_is_at_most_120_chars(self):
        long_out = "I don't have direct access to fetch that URL. " + "x" * 500
        turn = {"turn_id": "long1", "out": long_out}
        scanner = ConfabScanner(current_year=2026)
        matches = scanner.scan([turn])
        for m in matches:
            assert len(m.output_preview) <= 120
