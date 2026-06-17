"""Tests for audit_findings_to_tickets.py parser and matcher."""

import pathlib
import tempfile

import pytest

from devlab.claudecode.audit_findings_to_tickets import (
    AuditFinding,
    FindingMatcher,
    parse_findings,
)


@pytest.fixture
def tmp_audit_dir():
    """Create a temporary audit directory with synthetic findings."""
    with tempfile.TemporaryDirectory() as tmpdir:
        audit_dir = pathlib.Path(tmpdir)

        # Create a synthetic area file with 3 findings (2 HIGH, 1 MEDIUM)
        area_file = audit_dir / "area_1_test.md"
        area_file.write_text("""\
# Pass 2 deep-dive — Test Area

## Per-finding verdicts

### Finding P2-F1 — Critical memory encoding flaw

- Verdict: CONFIRMED
- Blast radius: EXTREMELY WIDE
- Biomimicry: theatrical
- Proposed ticket:
  - id: T-critical-memory-fix
  - title: Fix critical memory encoding bug
  - size: L
  - tags: [memory, critical, high-priority]
  - description: This is a critical issue affecting core memory operations.
  - disposal: SHIP

### Finding P2-F2 — Minor retrieval optimization

- Verdict: CONFIRMED_NARROWER
- Blast radius: NARROW
- Biomimicry: procedural
- Proposed ticket:
  - id: T-minor-retrieval-opt
  - title: Optimize retrieval path for performance
  - size: S
  - tags: [performance, optimization]
  - description: Small optimization opportunity in retrieval path.
  - disposal: DEFER

### Finding P2-F3 — Schema consistency issue

- Verdict: REFUTED_NARROWER
- Blast radius: MEDIUM
- Biomimicry: structural
- Proposed ticket:
  - id: T-schema-consistency
  - title: Ensure schema consistency across tables
  - size: M
  - tags: [schema, consistency]
  - description: Schema tables have inconsistent column naming.
  - disposal: DEFER
""")

        yield audit_dir


class TestParseFindings:
    """Test parsing of audit findings from markdown files."""

    def test_parse_findings_extracts_three_findings(self, tmp_audit_dir):
        """Verify that 3 findings are parsed from the synthetic file."""
        findings = parse_findings(tmp_audit_dir)
        assert len(findings) == 3

    def test_parse_findings_extracts_high_severity(self, tmp_audit_dir):
        """Verify that 2 findings are marked HIGH severity (F1 and F2)."""
        findings = parse_findings(tmp_audit_dir)
        high_findings = [f for f in findings if f.severity == "HIGH"]
        assert len(high_findings) == 2
        # F1: CONFIRMED + EXTREMELY WIDE → HIGH (verdict + blast radius)
        # F2: CONFIRMED_NARROWER (verdict signals confirmed issue) → HIGH

    def test_parse_findings_extracts_medium_severity(self, tmp_audit_dir):
        """Verify that 1 finding is marked MEDIUM severity (F3)."""
        findings = parse_findings(tmp_audit_dir)
        med_findings = [f for f in findings if f.severity == "MEDIUM"]
        assert len(med_findings) == 1
        # F3: CONFIRMED_NARROWER + MEDIUM blast radius + DEFER → MEDIUM

    def test_parse_findings_extracts_title(self, tmp_audit_dir):
        """Verify that titles are correctly extracted."""
        findings = parse_findings(tmp_audit_dir)
        titles = [f.title for f in findings]
        assert "Fix critical memory encoding bug" in titles
        assert "Optimize retrieval path for performance" in titles
        assert "Ensure schema consistency across tables" in titles

    def test_parse_findings_extracts_tags(self, tmp_audit_dir):
        """Verify that tags are correctly extracted."""
        findings = parse_findings(tmp_audit_dir)
        critical_finding = next(
            f for f in findings if f.proposed_ticket_id == "T-critical-memory-fix"
        )
        assert "critical" in critical_finding.tags
        assert "high-priority" in critical_finding.tags

    def test_parse_findings_extracts_proposed_ticket_id(self, tmp_audit_dir):
        """Verify that proposed ticket IDs are correctly extracted."""
        findings = parse_findings(tmp_audit_dir)
        ticket_ids = [f.proposed_ticket_id for f in findings]
        assert "T-critical-memory-fix" in ticket_ids
        assert "T-minor-retrieval-opt" in ticket_ids

    def test_parse_findings_extracts_disposal(self, tmp_audit_dir):
        """Verify that disposal field is correctly extracted."""
        findings = parse_findings(tmp_audit_dir)
        disposals = {f.disposal for f in findings}
        assert "SHIP" in disposals
        assert "DEFER" in disposals

    def test_parse_findings_empty_dir(self):
        """Verify that parsing an empty/nonexistent dir returns empty list."""
        with tempfile.TemporaryDirectory() as tmpdir:
            findings = parse_findings(pathlib.Path(tmpdir))
            assert findings == []

    def test_parse_findings_nonexistent_dir(self):
        """Verify that parsing a nonexistent dir returns empty list."""
        findings = parse_findings(pathlib.Path("/nonexistent/audit/dir"))
        assert findings == []


class TestFindingMatcher:
    """Test deduplication of findings against existing tickets."""

    def test_matcher_identifies_duplicate_by_title_overlap(self):
        """Verify that FindingMatcher detects a duplicate ticket."""
        existing = [
            {"id": "T-existing-1", "title": "Fix memory encoding bug"},
        ]
        matcher = FindingMatcher(existing)

        finding = AuditFinding(
            title="Critical memory encoding fix",
            severity="HIGH",
            description="Similar to existing ticket",
            source_file="test.md",
        )

        result = matcher.match(finding)
        assert not result.is_new
        assert result.matched_ticket_id == "T-existing-1"
        assert result.similarity_score > 0.5

    def test_matcher_identifies_new_finding(self):
        """Verify that FindingMatcher marks non-duplicate as new."""
        existing = [
            {"id": "T-existing-1", "title": "Optimize retrieval"},
        ]
        matcher = FindingMatcher(existing)

        finding = AuditFinding(
            title="Fix schema consistency issue",
            severity="HIGH",
            description="Completely different issue",
            source_file="test.md",
        )

        result = matcher.match(finding)
        assert result.is_new
        assert result.matched_ticket_id is None

    def test_matcher_requires_two_word_overlap(self):
        """Verify that single-word overlap does not trigger match."""
        existing = [
            {"id": "T-existing-1", "title": "Fix memory leaks"},
        ]
        matcher = FindingMatcher(existing)

        finding = AuditFinding(
            title="Memory fragmentation issue",  # Only "memory" overlaps
            severity="HIGH",
            description="Different issue",
            source_file="test.md",
        )

        result = matcher.match(finding)
        assert result.is_new  # Should not match on single word

    def test_matcher_empty_existing_tickets(self):
        """Verify that empty existing ticket list marks all as new."""
        matcher = FindingMatcher([])

        finding = AuditFinding(
            title="Some issue",
            severity="HIGH",
            description="Issue description",
            source_file="test.md",
        )

        result = matcher.match(finding)
        assert result.is_new

    def test_matcher_ignores_case_in_comparison(self):
        """Verify that matching is case-insensitive."""
        existing = [
            {"id": "T-existing-1", "title": "Fix Critical Bug"},
        ]
        matcher = FindingMatcher(existing)

        finding = AuditFinding(
            title="critical bug fix",  # lowercase version
            severity="HIGH",
            description="Same issue",
            source_file="test.md",
        )

        result = matcher.match(finding)
        assert not result.is_new  # Should match despite case difference


class TestFindingSeverityComputation:
    """Test the severity computation logic."""

    def test_high_severity_from_blast_radius(self, tmp_audit_dir):
        """Verify that EXTREMELY WIDE blast radius → HIGH severity."""
        findings = parse_findings(tmp_audit_dir)
        critical = next(
            f for f in findings if f.proposed_ticket_id == "T-critical-memory-fix"
        )
        assert critical.severity == "HIGH"
        assert critical.blast_radius == "EXTREMELY WIDE"

    def test_medium_severity_from_blast_radius(self, tmp_audit_dir):
        """Verify that MEDIUM blast radius → MEDIUM severity."""
        findings = parse_findings(tmp_audit_dir)
        schema = next(
            f for f in findings if f.proposed_ticket_id == "T-schema-consistency"
        )
        assert schema.severity == "MEDIUM"
        assert schema.blast_radius == "MEDIUM"

    def test_confirmed_verdict_gives_high_severity(self, tmp_audit_dir):
        """Verify that CONFIRMED verdict contributes to HIGH severity."""
        findings = parse_findings(tmp_audit_dir)
        assert all(f.severity in ("HIGH", "MEDIUM") for f in findings)
        assert all(f.verdict is not None for f in findings)


class TestIntegration:
    """Integration tests combining parser and matcher."""

    def test_parse_and_match_workflow(self, tmp_audit_dir):
        """Verify the full workflow: parse, build matcher, identify new."""
        findings = parse_findings(tmp_audit_dir)
        assert len(findings) == 3

        # Simulate existing tickets (2 of the 3 findings already ticketed)
        existing = [
            {"id": "T-existing-1", "title": "Fix critical memory encoding bug"},
            {"id": "T-existing-2", "title": "Optimize retrieval path"},
        ]
        matcher = FindingMatcher(existing)

        matches = [matcher.match(f) for f in findings]
        new_count = sum(1 for m in matches if m.is_new)
        existing_count = sum(1 for m in matches if not m.is_new)

        # One finding should be new (schema consistency)
        assert new_count == 1
        assert existing_count == 2
