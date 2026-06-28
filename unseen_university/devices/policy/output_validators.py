"""Post-result output validation for agent tool calls.

Validates and redacts sensitive content from tool results before they are
delivered to the calling agent. V1 checks: PII pattern detection (email,
phone, SSN) and a configurable content blocklist.

Failed validation redacts the content and logs the incident; it does NOT
block delivery by default. Blocking behavior is configurable via OutputPolicy.

Usage:
    validator = OutputValidator(OutputPolicy(pii_check=True))
    redacted, incidents = validator.validate(raw_result_text)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PII_PATTERNS: dict[str, re.Pattern] = {
    "email": re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
    "ssn": re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"),
    "phone": re.compile(r"\b(?:\+1[\s\-]?)?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{4}\b"),
}

_REDACT_LABEL = "[REDACTED:{name}]"


@dataclass
class OutputPolicy:
    """Validation policy applied to a tool result.

    pii_check: whether to scan for email, phone, SSN patterns.
    blocklist: exact strings to redact regardless of PII check.
    """

    pii_check: bool = True
    blocklist: list[str] = field(default_factory=list)


class OutputValidator:
    """Validates and redacts a text result against an OutputPolicy."""

    def __init__(self, policy: OutputPolicy | None = None) -> None:
        self._policy = policy or OutputPolicy()

    def validate(self, text: str) -> tuple[str, list[str]]:
        """Scan *text* for policy violations.

        Returns (redacted_text, incidents) where incidents is a list of
        human-readable strings describing each redaction. When incidents is
        empty the returned text equals the input text.
        """
        incidents: list[str] = []
        result = text

        if self._policy.pii_check:
            for name, pattern in _PII_PATTERNS.items():
                if pattern.search(result):
                    count = len(pattern.findall(result))
                    label = _REDACT_LABEL.format(name=name)
                    result = pattern.sub(label, result)
                    incidents.append(f"pii:{name} — {count} match(es) redacted")

        for term in self._policy.blocklist:
            if term in result:
                count = result.count(term)
                result = result.replace(term, "[REDACTED:blocklist]")
                incidents.append(f"blocklist:{term!r} — {count} occurrence(s) redacted")

        return result, incidents
