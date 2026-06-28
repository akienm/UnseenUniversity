"""Tests for post-result output validation hook in BaseShim.

Completion criteria:
  - A tool result containing an email address is redacted before delivery to
    the caller and the incident is logged.
  - Existing tool calls with clean output are unaffected.

Note: validate_output is unit-proven here via direct call. End-to-end wiring
(automatic interception at the result boundary) is blocked on T-shim-traffic-spy
which adds the dispatch path to the shim architecture.
"""

from __future__ import annotations

import logging

import pytest

from unseen_university.devices.policy.output_validators import OutputPolicy, OutputValidator
from unseen_university.shim import BaseShim

# ── Minimal concrete shim for testing ────────────────────────────────────────


class _StubShim(BaseShim):
    @property
    def device_id(self) -> str:
        return "stub"

    def start(self) -> bool:
        return True

    def stop(self) -> bool:
        return True

    def restart(self) -> bool:
        return True

    def self_test(self) -> dict:
        return {"passed": True, "details": "stub"}

    def rollback(self) -> None:
        pass


# ── OutputValidator unit tests ────────────────────────────────────────────────


class TestOutputValidatorEmail:
    def test_email_is_redacted(self):
        v = OutputValidator()
        text = "Contact us at support@example.com for help."
        redacted, incidents = v.validate(text)
        assert "support@example.com" not in redacted
        assert "[REDACTED:email]" in redacted
        assert any("email" in i for i in incidents)

    def test_multiple_emails_all_redacted(self):
        v = OutputValidator()
        text = "From alice@example.com to bob@test.org"
        redacted, incidents = v.validate(text)
        assert "alice@example.com" not in redacted
        assert "bob@test.org" not in redacted

    def test_clean_text_unchanged(self):
        v = OutputValidator()
        text = "No sensitive data here."
        redacted, incidents = v.validate(text)
        assert redacted == text
        assert incidents == []


class TestOutputValidatorSSN:
    def test_ssn_with_dashes_redacted(self):
        v = OutputValidator()
        text = "SSN: 123-45-6789"
        redacted, incidents = v.validate(text)
        assert "123-45-6789" not in redacted
        assert "[REDACTED:ssn]" in redacted

    def test_ssn_plain_digits_redacted(self):
        v = OutputValidator()
        text = "Social security 123456789 found"
        redacted, incidents = v.validate(text)
        assert "123456789" not in redacted


class TestOutputValidatorPhone:
    def test_us_phone_redacted(self):
        v = OutputValidator()
        text = "Call me at 555-867-5309"
        redacted, incidents = v.validate(text)
        assert "555-867-5309" not in redacted
        assert any("phone" in i for i in incidents)


class TestOutputValidatorBlocklist:
    def test_blocklist_term_redacted(self):
        v = OutputValidator(OutputPolicy(pii_check=False, blocklist=["SECRET_TOKEN"]))
        text = "Use SECRET_TOKEN to authenticate"
        redacted, incidents = v.validate(text)
        assert "SECRET_TOKEN" not in redacted
        assert "[REDACTED:blocklist]" in redacted

    def test_pii_disabled_skips_patterns(self):
        v = OutputValidator(OutputPolicy(pii_check=False))
        text = "Email: admin@corp.com"
        redacted, incidents = v.validate(text)
        assert redacted == text
        assert incidents == []


# ── BaseShim.validate_output integration ─────────────────────────────────────


class TestBaseShimValidateOutput:
    def test_no_validator_passthrough(self):
        shim = _StubShim()
        text = "Contact admin@secret.com immediately"
        assert shim.validate_output(text) == text

    def test_non_string_passthrough(self):
        shim = _StubShim()
        shim._output_validator = OutputValidator()
        result = {"data": "admin@secret.com"}
        assert shim.validate_output(result) is result

    def test_email_redacted_when_validator_set(self):
        shim = _StubShim()
        shim._output_validator = OutputValidator()
        text = "Contact admin@secret.com immediately"
        out = shim.validate_output(text)
        assert "admin@secret.com" not in out
        assert "[REDACTED:email]" in out

    def test_clean_output_unchanged_when_validator_set(self):
        shim = _StubShim()
        shim._output_validator = OutputValidator()
        text = "Everything looks fine here."
        assert shim.validate_output(text) == text

    def test_incident_logged(self, caplog):
        shim = _StubShim()
        shim._output_validator = OutputValidator()
        text = "email: leak@example.com"
        with caplog.at_level(logging.WARNING, logger="unseen_university.shim"):
            shim.validate_output(text)
        assert any("email" in r.message for r in caplog.records)
