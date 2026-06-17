#!/usr/bin/env python3
"""
audit_findings_to_tickets.py — Parse audit pass-2 output and surface severity-high findings as tickets.

Scans audit_2026/pass2_output/*.md files for structured findings with severity markers.
Detects findings not yet ticketed via FindingMatcher (title similarity against existing queue).
Surfaces new findings and optionally auto-files them as tickets via cc_queue.py.

Expected audit format:
  ## Finding <id> — <title>
  - Verdict: <status>
  - Blast radius: <size>
  - [other fields...]
  - Proposed ticket:
    - id: T-<id>
    - title: <title>
    - size: <size>
    - tags: [<tag>, ...]
    - description: <multi-line description>
    - disposal: SHIP | INVESTIGATE | DEFER | DISCARD

Severity extracted from:
  1. Literal "SEVERITY: HIGH" / "SEVERITY: MEDIUM" / "SEVERITY: LOW" markers
  2. Fallback: Blast radius → {EXTREMELY WIDE, WIDE, MEDIUM, NARROW} ≈ {HIGH, HIGH, MED, LOW}
  3. Fallback: Verdict status confirmation → {CONFIRMED, CONFIRMED_NARROWER} ≈ HIGH severity
  4. Fallback: "disposal: SHIP" ≈ HIGH (file it immediately)

Deduplication: FindingMatcher compares new findings against existing queue titles.
Output: Prints findings grouped by severity; writes a findings slate entry.
"""

import dataclasses
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Optional

# ============================================================================
# Data structures
# ============================================================================


@dataclasses.dataclass
class AuditFinding:
    """A single audit finding extracted from markdown."""

    title: str
    severity: str  # "HIGH", "MEDIUM", "LOW"
    description: str
    source_file: str
    finding_id: Optional[str] = None  # e.g., "P2-F1"
    verdict: Optional[str] = None  # e.g., "CONFIRMED"
    blast_radius: Optional[str] = None  # e.g., "EXTREMELY WIDE"
    proposed_ticket_id: Optional[str] = None  # e.g., "T-engrams-as-ensembles"
    tags: list = dataclasses.field(default_factory=list)
    disposal: Optional[str] = None  # e.g., "SHIP", "INVESTIGATE"

    def __repr__(self) -> str:
        return f"AuditFinding(id={self.finding_id}, sev={self.severity}, title={self.title[:50]}...)"


@dataclasses.dataclass
class MatchResult:
    """Result of matching a finding against existing tickets."""

    finding: AuditFinding
    matched_ticket_id: Optional[str] = None
    similarity_score: float = 0.0
    is_new: bool = True


# ============================================================================
# Parser
# ============================================================================


def parse_findings(audit_dir: pathlib.Path) -> list[AuditFinding]:
    """
    Parse *.md files in audit_dir, extract findings with severity markers.

    Returns list of AuditFinding objects for all findings encountered.
    If audit_dir does not exist or contains no files, returns empty list.
    """
    findings = []

    if not audit_dir.exists():
        return findings

    for md_file in sorted(audit_dir.glob("*.md")):
        # Skip AGGREGATE and other non-finding files
        if "AGGREGATE" in md_file.name or "prompt" in md_file.name:
            continue

        text = md_file.read_text(encoding="utf-8", errors="ignore")
        findings.extend(_parse_file(text, str(md_file)))

    return findings


def _parse_file(text: str, source_file: str) -> list[AuditFinding]:
    """Parse a single markdown file and extract findings."""
    findings = []

    # Split by finding headers: ### Finding <id> — <title>
    pattern = r"^### Finding ([\w\-]+)\s+—\s+(.+)$"
    matches = list(re.finditer(pattern, text, re.MULTILINE))

    if not matches:
        return findings

    for i, match in enumerate(matches):
        start = match.start()
        # End of this finding = start of next finding, or end of file
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        finding_block = text[start:end]

        finding_id = match.group(1)
        finding_title = match.group(2).strip()

        # Extract structured fields from this block
        verdict = _extract_field(finding_block, "Verdict")
        blast_radius = _extract_field(finding_block, "Blast radius")
        biomimicry = _extract_field(finding_block, "Biomimicry")
        disposal = _extract_field(finding_block, "disposal")

        # Compute severity from available signals
        severity = _compute_severity(verdict, blast_radius, disposal, finding_block)

        # Extract proposed ticket info (if present)
        ticket_title = None
        ticket_id = None
        ticket_size = None
        ticket_tags = []
        ticket_desc = None

        proposed_section = _extract_proposed_ticket(finding_block)
        if proposed_section:
            ticket_id = _extract_yaml_field(proposed_section, "id")
            ticket_title = _extract_yaml_field(proposed_section, "title")
            ticket_size = _extract_yaml_field(proposed_section, "size")
            ticket_tags = _extract_yaml_list_field(proposed_section, "tags")
            ticket_desc = _extract_yaml_field(proposed_section, "description")
            # Extraction of disposal from proposed ticket section if not found at top-level
            if not disposal:
                disposal = _extract_yaml_field(proposed_section, "disposal")

        # Build description from verdict + biomimicry + disposal + proposed ticket
        description_parts = [finding_title]
        if verdict:
            description_parts.append(f"Verdict: {verdict}")
        if blast_radius:
            description_parts.append(f"Blast radius: {blast_radius}")
        if biomimicry:
            description_parts.append(f"Biomimicry: {biomimicry}")
        if ticket_desc:
            description_parts.append(f"Description: {ticket_desc}")
        if disposal:
            description_parts.append(f"Disposal: {disposal}")

        full_description = "\n\n".join(description_parts)

        finding = AuditFinding(
            title=ticket_title or finding_title,
            severity=severity,
            description=full_description,
            source_file=source_file,
            finding_id=finding_id,
            verdict=verdict,
            blast_radius=blast_radius,
            proposed_ticket_id=ticket_id,
            tags=ticket_tags,
            disposal=disposal,
        )
        findings.append(finding)

    return findings


def _extract_field(text: str, field_name: str) -> Optional[str]:
    """Extract a top-level field value (e.g., 'Verdict: CONFIRMED')."""
    pattern = rf"^- {re.escape(field_name)}:\s*(.+)$"
    match = re.search(pattern, text, re.MULTILINE)
    return match.group(1).strip() if match else None


def _extract_proposed_ticket(text: str) -> Optional[str]:
    """Extract the 'Proposed ticket:' section (from that line to end or next main section)."""
    pattern = r"^- Proposed ticket:\n(.+?)(?=^-|-Verdict|^### Finding|\Z)"
    match = re.search(pattern, text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else None


def _extract_yaml_field(yaml_block: str, key: str) -> Optional[str]:
    """Extract a YAML-style field from a block (e.g., '  - id: T-foo')."""
    pattern = rf"^\s*- {re.escape(key)}:\s*(.+)$"
    match = re.search(pattern, yaml_block, re.MULTILINE)
    if match:
        val = match.group(1).strip()
        # If value is on same line, use it; else multiline desc follows
        if not val.startswith("["):
            return val
        # For multiline strings, grab until next '-' at same indent
        lines = [val]
        start_line = match.end()
        for line in yaml_block[start_line:].split("\n"):
            if line.strip().startswith("- "):
                break
            if line.strip():
                lines.append(line.strip())
        return " ".join(lines).strip()
    return None


def _extract_yaml_list_field(yaml_block: str, key: str) -> list[str]:
    """Extract a YAML list field (e.g., '  - tags: [a, b, c]')."""
    pattern = rf"^\s*- {re.escape(key)}:\s*\[(.+?)\]"
    match = re.search(pattern, yaml_block, re.MULTILINE)
    if match:
        items = match.group(1)
        return [s.strip() for s in items.split(",")]
    return []


def _compute_severity(
    verdict: Optional[str],
    blast_radius: Optional[str],
    disposal: Optional[str],
    text: str,
) -> str:
    """
    Compute severity from available signals.

    Priority:
    1. Explicit "SEVERITY: HIGH" in text
    2. Verdict: CONFIRMED* → HIGH
    3. Blast radius: EXTREMELY WIDE, WIDE → HIGH; MEDIUM → MED; NARROW → LOW
    4. Disposal: SHIP → HIGH; INVESTIGATE → HIGH; DEFER → MED; DISCARD → LOW
    5. Default: MEDIUM (findings with no explicit markers)
    """
    # Check for explicit severity marker
    if "SEVERITY: HIGH" in text or "severity: high" in text.lower():
        return "HIGH"
    if "SEVERITY: MEDIUM" in text or "severity: medium" in text.lower():
        return "MEDIUM"
    if "SEVERITY: LOW" in text or "severity: low" in text.lower():
        return "LOW"

    # Verdict: CONFIRMED* → HIGH
    if verdict and "CONFIRMED" in verdict:
        return "HIGH"

    # Blast radius
    if blast_radius:
        if any(w in blast_radius.upper() for w in ["EXTREMELY", "VERY", "WIDE"]):
            return "HIGH"
        if "MEDIUM" in blast_radius.upper():
            return "MEDIUM"
        if "NARROW" in blast_radius.upper():
            return "LOW"

    # Disposal: SHIP/INVESTIGATE → HIGH, DEFER → MEDIUM, DISCARD → LOW
    if disposal:
        if disposal.upper() in ("SHIP", "INVESTIGATE"):
            return "HIGH"
        if "DEFER" in disposal.upper():
            return "MEDIUM"
        if "DISCARD" in disposal.upper():
            return "LOW"

    # Default
    return "MEDIUM"


# ============================================================================
# Matching and deduplication
# ============================================================================


class FindingMatcher:
    """
    Match audit findings against existing tickets to detect duplicates.

    Uses simple title-similarity heuristic (word overlap).
    Threshold: if 2+ words match (case-insensitive, excluding common words),
    findings are considered likely duplicates.
    """

    COMMON_WORDS = {
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "from",
        "is",
        "are",
        "be",
        "been",
        "being",
        "have",
        "has",
        "do",
        "does",
        "did",
        "will",
        "would",
        "should",
        "could",
        "may",
        "might",
        "can",
        "must",
        "shall",
        "of",
        "in",
        "at",
        "on",
        "for",
        "with",
        "by",
        "about",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "under",
        "again",
        "further",
        "then",
        "once",
    }

    def __init__(self, existing_tickets: list[dict]):
        """
        Initialize with list of existing tickets from cc_queue.

        Expected format: [{'id': 'T-foo', 'title': 'Fix foo...', ...}, ...]
        """
        self.existing_tickets = existing_tickets or []

    def match(self, finding: AuditFinding) -> MatchResult:
        """
        Match a finding against existing tickets.

        Returns MatchResult with matched_ticket_id if duplicate found, else is_new=True.
        """
        if not self.existing_tickets:
            return MatchResult(finding=finding, is_new=True)

        finding_words = self._tokenize(finding.title)
        best_match = None
        best_score = 0.0

        for ticket in self.existing_tickets:
            ticket_title = ticket.get("title") or ""
            ticket_words = self._tokenize(ticket_title)

            # Count common words (excluding stop words)
            overlap = len(finding_words & ticket_words)
            if overlap >= 2:
                similarity = overlap / max(len(finding_words), len(ticket_words))
                if similarity > best_score:
                    best_score = similarity
                    best_match = ticket.get("id")

        if best_match and best_score >= 0.5:
            return MatchResult(
                finding=finding,
                matched_ticket_id=best_match,
                similarity_score=best_score,
                is_new=False,
            )

        return MatchResult(finding=finding, is_new=True)

    @classmethod
    def _tokenize(cls, text: str) -> set[str]:
        """Tokenize text into lowercase words, filter stop words and short words."""
        words = re.findall(r"\b\w+\b", text.lower())
        return {w for w in words if w not in cls.COMMON_WORDS and len(w) > 2}


# ============================================================================
# Main entry point
# ============================================================================


def load_existing_tickets() -> list[dict]:
    """Load existing tickets from cc_queue.py list output."""
    try:
        result = subprocess.run(
            [
                "python3",
                str(Path(__file__).parent / "cc_queue.py"),
                "list",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return []

        tickets = []
        for line in result.stdout.splitlines():
            # Format: "⬜ [T-id] (size) ... title ... [status]"
            # Extract ID and title
            match = re.search(
                r"\[([^\]]+)\].*?: (.+?)\s+\[(pending|in_progress|done|blocked|needs_review|awaiting_approval)\]",
                line,
            )
            if match:
                ticket_id = match.group(1)
                ticket_title = match.group(2).strip()
                # Skip DESIGNED: prefix, GH# prefix, etc. for cleaner matching
                ticket_title = re.sub(
                    r"^(DESIGNED:|NEW:|GH#\d+|GH#\d+\s+)", "", ticket_title
                ).strip()
                tickets.append({"id": ticket_id, "title": ticket_title})

        return tickets
    except Exception as e:
        print(f"Warning: failed to load existing tickets: {e}", file=sys.stderr)
        return []


def main():
    """Parse audit findings, match against existing tickets, surface new ones."""
    audit_dir = (
        pathlib.Path.home() / "devlab" / "design_docs" / "audit_2026" / "pass2_output"
    )

    # Parse all findings
    findings = parse_findings(audit_dir)
    if not findings:
        print(
            "No audit findings parsed — audit_2026/pass2_output may be empty or missing."
        )
        return

    # Load existing tickets and build matcher
    existing = load_existing_tickets()
    matcher = FindingMatcher(existing)

    # Match each finding
    high_new = []
    high_matched = []
    med = []
    low = []

    for finding in findings:
        match_result = matcher.match(finding)
        if finding.severity == "HIGH":
            if match_result.is_new:
                high_new.append((finding, match_result))
            else:
                high_matched.append((finding, match_result))
        elif finding.severity == "MEDIUM":
            med.append((finding, match_result))
        else:
            low.append((finding, match_result))

    # Print findings report
    print("\n" + "=" * 80)
    print("AUDIT FINDINGS REPORT")
    print("=" * 80)

    if high_new:
        print(f"\n🔴 NEW SEVERITY-HIGH findings ({len(high_new)}) — FILE TICKETS:")
        for finding, _ in high_new:
            print(f"  • {finding.title}")
            if finding.proposed_ticket_id:
                print(f"    (proposed: {finding.proposed_ticket_id})")
            if finding.disposal:
                print(f"    (disposal: {finding.disposal})")

    if high_matched:
        print(f"\n🟡 ALREADY-TICKETED severity-high findings ({len(high_matched)}):")
        for finding, match_result in high_matched:
            print(f"  • {finding.title}")
            print(
                f"    (matched: {match_result.matched_ticket_id}, similarity: {match_result.similarity_score:.2%})"
            )

    if med:
        print(f"\n🟠 SEVERITY-MEDIUM findings ({len(med)}) — CONSIDER TICKETING:")
        for finding, _ in med:
            print(f"  • {finding.title}")

    if low:
        print(f"\n🟢 SEVERITY-LOW findings ({len(low)}) — NOTE:")
        for finding, _ in low:
            print(f"  • {finding.title}")

    print("\n" + "=" * 80)
    print(
        f"Summary: {len(high_new)} NEW HIGH | {len(high_matched)} existing HIGH | {len(med)} MED | {len(low)} LOW"
    )
    print("=" * 80 + "\n")

    # If HIGH findings exist, return exit code 1 to signal action needed
    if high_new:
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
