"""
test_privacy_sentinel_filter.py — T-stored-locally-only-contact-defect (#416)

Tests for the privacy-sentinel detection in main._is_raw_tool_leak.
The sentinel patterns are tool return values that should never leak to
the user as Igor's reply text.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wild_igor.igor.main import _is_raw_tool_leak  # noqa: E402

# ── Privacy sentinel detection ───────────────────────────────────────────────


def test_stored_locally_only_contact_detected():
    """The exact sentinel from Igor's observed emit leak."""
    assert _is_raw_tool_leak("stored_locally_only:CONTACT_375375DD48") is True


def test_stored_locally_only_with_other_id_format():
    assert _is_raw_tool_leak("stored_locally_only:abc123") is True


def test_stored_locally_google_error_sentinel_detected():
    """The error variant from google_contacts when Google API raises."""
    assert _is_raw_tool_leak("stored_locally|google_error:HTTP 403") is True


def test_sentinel_with_leading_whitespace_detected():
    """Leading whitespace should not mask the sentinel."""
    assert _is_raw_tool_leak("  stored_locally_only:CONTACT_x") is True


def test_sentinel_case_insensitive():
    """Uppercase variants should still be caught."""
    assert _is_raw_tool_leak("STORED_LOCALLY_ONLY:CONTACT_x") is True


def test_plain_text_about_contacts_not_sentinel():
    """Sentences that mention contacts casually should NOT be filtered."""
    assert _is_raw_tool_leak("I'll store that contact locally only for now.") is False


def test_plain_reply_not_filtered():
    assert _is_raw_tool_leak("Sure, I can help with that.") is False


def test_empty_string_not_filtered():
    assert _is_raw_tool_leak("") is False


# ── Backward compatibility: existing leak patterns still caught ─────────────


def test_run_bash_result_leak_still_caught():
    assert _is_raw_tool_leak('[run_bash result: {"exit_code": 0}]') is True


def test_bash_result_plain_text_leak_caught():
    """Variant from Igor 2026-04-15: [bash result: filename.txt...] leaks
    plain text (not JSON) tool output."""
    assert (
        _is_raw_tool_leak(
            "[bash result: architecture_root.dsb\ncapabilities_index.dsb]"
        )
        is True
    )


def test_bash_result_no_output_caught():
    assert _is_raw_tool_leak("[bash result: (no output)]") is True


def test_csb_tool_leak_still_caught():
    assert _is_raw_tool_leak("[check_process] NOT_RUNNING|name=foo") is True


def test_regular_bracketed_text_not_leak():
    """A reply that legitimately starts with a bracketed word shouldn't be
    caught by the tool-leak patterns."""
    assert _is_raw_tool_leak("[note] I learned something interesting.") is False


# ── T-interceptor-habit-hijacks-reply-path: bare NOT_RUNNING|... ─────────────


def test_bare_not_running_template_caught():
    """Observed 2026-04-24: check_process returned 'NOT_RUNNING|name=<user_msg>'
    with no [tool_name] prefix, and that leaked as the reply. Bare CSB templates
    must be caught too."""
    leak = (
        "NOT_RUNNING|name=[Web message from akien]: we are sprinting "
        "toward igor can process a ticket on his own."
    )
    assert _is_raw_tool_leak(leak) is True


def test_bare_running_template_caught():
    assert (
        _is_raw_tool_leak("RUNNING|name=igor|count=1|pids=12345|processes=python")
        is True
    )


def test_bare_ok_and_fail_templates_caught():
    assert _is_raw_tool_leak("OK|step=install|result=applied") is True
    assert _is_raw_tool_leak("FAIL|step=restart|reason=timeout") is True


def test_prose_with_pipe_is_not_leak():
    """Legitimate prose that happens to contain a pipe should not be caught.
    The bare-form regex requires STATUS|key= (key-value structure) so free-form
    prose with a pipe stays safe."""
    assert _is_raw_tool_leak("Options: keep it | discard it | ask Akien.") is False


def test_bare_template_without_kv_not_caught():
    """Bare 'NOT_RUNNING|' without a key=value pair is not a tool template —
    could be someone quoting a status name. Only the key= form is a real leak."""
    assert _is_raw_tool_leak("NOT_RUNNING|something informal here") is False
