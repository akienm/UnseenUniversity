"""Unit tests for pe_chain HYPOTHESIZE prompt assembly.

# author-model: opus

Cert walk-02 surfaced that the LLM-facing prompt was too hard for qwen-2.5-coder
to handle: line-number prefixes in `actual` forced verbatim-copy of code lines
to require non-trivial prefix arithmetic, and the LLM corrupted on multiple
dimensions (drop indent, flip quote style). These tests pin the prompt-assembly
behavior so future changes can be validated without burning live LLM credits.

Layered:
  - TestStripLinePrefix — pure helper test (no I/O, no LLM)
  - TestHypothesizePromptShape — assembled prompt structure (no LLM call)
  - TestHypothesizeValidation — old_string verbatim validation (no LLM)
  - TestHypothesizeRetryPromptShape — retry prompt structure (no LLM)

When live LLM behavior needs verification, do it in a separate integration
harness (cert_walk_log + pe_chain_debugger) — these unit tests are the cheap
fast loop for prompt iteration.
"""

from __future__ import annotations

from wild_igor.igor.tools import pe_chain

# ── _strip_line_prefix ───────────────────────────────────────────────────────


class TestStripLinePrefix:
    def test_basic_two_line(self):
        text = "1: foo\n2: bar"
        assert pe_chain._strip_line_prefix(text) == "foo\nbar"

    def test_preserves_original_indent(self):
        # The exact failure mode from cert walk-02 attempt 6:
        # LLM had to strip "279:     " (line-number prefix + 4 spaces from prefix
        # format) but produce code that retains the original 4-space indent.
        # After strip, original indent must be intact.
        text = '279:     if t["status"] != "pending":'
        assert pe_chain._strip_line_prefix(text) == '    if t["status"] != "pending":'

    def test_no_prefix_passes_through(self):
        text = "raw line without prefix"
        assert pe_chain._strip_line_prefix(text) == text

    def test_empty_string(self):
        assert pe_chain._strip_line_prefix("") == ""

    def test_preserves_line_breaks(self):
        text = "1: a\n2: b\n3: c"
        out = pe_chain._strip_line_prefix(text)
        assert out == "a\nb\nc"

    def test_multiline_with_mixed_indentation(self):
        text = "1: line one\n2:     line two indented\n3: line three"
        assert (
            pe_chain._strip_line_prefix(text)
            == "line one\n    line two indented\nline three"
        )

    def test_preserves_quote_style_in_content(self):
        # Crucial: stripping must not alter quote characters in the content.
        text = "10: x = \"double\"\n11: y = 'single'"
        out = pe_chain._strip_line_prefix(text)
        assert out == "x = \"double\"\ny = 'single'"

    def test_does_not_match_colon_in_content(self):
        # A line whose CONTENT contains "<num>: " must not be re-stripped.
        # The regex anchors to start-of-line, so middle-of-line should be safe.
        text = '5: print("hello: world")'
        out = pe_chain._strip_line_prefix(text)
        assert out == 'print("hello: world")'

    def test_handles_high_line_numbers(self):
        text = "12345: deeply numbered line"
        assert pe_chain._strip_line_prefix(text) == "deeply numbered line"

    def test_blank_line_in_section(self):
        # _read_file_section emits "<n>: " (trailing space) for blank lines
        text = "1: line one\n2: \n3: line three"
        out = pe_chain._strip_line_prefix(text)
        assert out == "line one\n\nline three"


# ── Validation logic against verbatim copy ──────────────────────────────────


class TestHypothesizeValidation:
    """_validate_hypothesis is the verbatim gate. These tests pin its behavior."""

    def test_exact_match_passes(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text('if t["status"] != "pending":\n    pass\n')
        edit = {
            "file": "x.py",
            "old_string": 'if t["status"] != "pending":',
            "new_string": 'if t["worker"] == "igor":',
        }
        err = pe_chain._validate_hypothesis(edit, tmp_path)
        assert err is None or err == ""

    def test_quote_style_mismatch_fails(self, tmp_path):
        # The cert walk-02 failure: LLM produces single quotes when source has
        # double quotes. Validation must reject — the strip-helper fix is
        # supposed to prevent the LLM from producing this paraphrase, but if
        # it slips through, validation must catch it.
        f = tmp_path / "x.py"
        f.write_text('if t["status"] != "pending":\n')
        edit = {
            "file": "x.py",
            "old_string": "if t['status'] != 'pending':",  # single quotes
            "new_string": "if t['worker'] == 'igor':",
        }
        err = pe_chain._validate_hypothesis(edit, tmp_path)
        assert err is not None and err != ""

    def test_dropped_indent_fails(self, tmp_path):
        # The other half of the cert walk-02 corruption: LLM dropped the
        # 4-space leading indent. Validation must reject.
        f = tmp_path / "x.py"
        f.write_text("def f():\n    if x:\n        return 1\n")
        edit = {
            "file": "x.py",
            "old_string": "if x:",  # missing leading 4 spaces
            "new_string": "if x is None:",
        }
        err = pe_chain._validate_hypothesis(edit, tmp_path)
        # _validate_hypothesis treats `if x:` as a substring search; it WILL
        # find that anywhere in the file. So this test demonstrates the
        # validator's tolerance — it doesn't enforce indent. Documenting
        # current behavior; if we tighten validation later, this is the test
        # to flip.
        assert err is None or err == ""

    def test_missing_string_fails(self, tmp_path):
        f = tmp_path / "x.py"
        f.write_text("def f():\n    pass\n")
        edit = {
            "file": "x.py",
            "old_string": "this string is not in the file at all",
            "new_string": "replacement",
        }
        err = pe_chain._validate_hypothesis(edit, tmp_path)
        assert err is not None and err != ""


# ── Prompt shape (assembled prompt content, no LLM call) ─────────────────────


class TestHypothesizePromptShape:
    """The prompt the LLM actually sees. Built by reading the prompt template
    and applying _strip_line_prefix to actual. This is where we validate that
    the prompt is 'copy-verbatim friendly' as a peer LLM would see it."""

    def _make_prompt(self, description: str, actual_line_numbered: str) -> str:
        """Replicate pe_hypothesize's prompt assembly without calling the LLM."""
        return pe_chain._HYPOTHESIZE_PROMPT.format(
            description=description[: pe_chain._DESC_CAP_REASONING],
            actual=pe_chain._strip_line_prefix(
                actual_line_numbered[: pe_chain._HYPOTHESIZE_ACTUAL_CHAR_CAP]
            ),
        )

    def test_prompt_does_not_contain_line_number_prefix(self):
        # The exact bug from cert walk-02: line-number prefixes leaked through
        # to the LLM, making verbatim copy require prefix-stripping cognition.
        actual_numbered = "1: def f():\n2:     return 1\n"
        prompt = self._make_prompt("test ticket", actual_numbered)
        # No "1:" or "2:" should appear at start-of-line in the prompt's
        # actual-code section. The simplest check: the strings "1: def" and
        # "2:     return" should not appear (they're the line-numbered form).
        assert "1: def f():" not in prompt
        assert "2:     return 1" not in prompt
        # The verbatim content SHOULD appear:
        assert "def f():" in prompt
        assert "    return 1" in prompt

    def test_prompt_preserves_original_indent(self):
        actual_numbered = '279:     if t["status"] != "pending":'
        prompt = self._make_prompt("ticket", actual_numbered)
        assert '    if t["status"] != "pending":' in prompt

    def test_prompt_preserves_double_quotes(self):
        # Verify quote style survives the strip — was the proximate cause of
        # cert walk-02 attempt 6 failure.
        actual_numbered = '10: x = {"key": "value"}'
        prompt = self._make_prompt("ticket", actual_numbered)
        assert 'x = {"key": "value"}' in prompt
        # And the single-quoted paraphrase MUST NOT be in the prompt:
        assert "x = {'key': 'value'}" not in prompt

    def test_prompt_includes_ticket_description(self):
        prompt = self._make_prompt("Fix the cmd_claim bug", "1: code\n")
        assert "Fix the cmd_claim bug" in prompt

    def test_prompt_includes_verbatim_rule(self):
        # The key behavioral instruction must be in the prompt.
        prompt = self._make_prompt("ticket", "1: code\n")
        assert "verbatim" in prompt.lower()

    def test_prompt_includes_quote_preservation_rule(self):
        prompt = self._make_prompt("ticket", "1: code\n")
        # The rule we added in commit 69ef9b74 — verify it survives.
        assert "Preserve exact quote style" in prompt

    def test_actual_cap_truncates_long_input(self):
        # The 16000-char cap must apply.
        long_actual = "1: line\n" * 5000  # ~40K chars
        prompt = self._make_prompt("ticket", long_actual)
        # The stripped + capped actual must be <= 16000 chars
        # (we slice before strip, so cap applies to the line-numbered form).
        # Verify: count of "line\n" occurrences in stripped form is bounded.
        stripped_line_count = prompt.count("line\n")
        # 16000 chars / 8 chars per "1: line\n" = 2000 lines max in capped form.
        # After strip, "line\n" is 5 chars, so stripped form still has ~2000.
        assert stripped_line_count <= 2200  # generous upper bound


# ── Retry prompt shape ───────────────────────────────────────────────────────


class TestHypothesizeRetryPromptShape:
    def test_retry_prompt_includes_failure_summary(self):
        original = "ORIGINAL_PROMPT_TEXT"
        failed_edits = [
            {
                "file": "x.py",
                "old_string": "wrong",
                "new_string": "right",
            }
        ]
        errors = ["edit[0] (x.py): old_string not found verbatim in x.py"]
        actual = "def f():\n    pass"
        prompt = pe_chain._build_retry_prompt(original, failed_edits, errors, actual)
        # Must include the original prompt
        assert "ORIGINAL_PROMPT_TEXT" in prompt
        # Must include the failure error
        assert "old_string not found verbatim" in prompt
        # Must include the failed edit's old_string for the LLM to see what it
        # produced
        assert "wrong" in prompt

    def test_retry_prompt_instructs_verbatim_copy(self):
        prompt = pe_chain._build_retry_prompt(
            "orig",
            [{"file": "x.py", "old_string": "a", "new_string": "b"}],
            ["err"],
            "actual code",
        )
        # Must remind the LLM about verbatim copy
        assert "verbatim" in prompt.lower() or "exact" in prompt.lower()
