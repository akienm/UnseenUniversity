"""confab_scanner — detect confabulation tell-phrases in recent Igor turns.

Three subtypes, each with a tell-phrase library. Phrases are shapes the LLM
reaches for when it has no grounding anchor in memory:

  capability — "I don't have direct access to X", "I can't fetch", "I'm just
               an LLM" — architectural excuses for missing capability.
               Reality: Igor has tools; channel doesn't gate them.

  fact       — temporal drift (e.g. "April 2025" when the date is 2026-04-23),
               wrong version numbers. LLM falls back to training-corpus priors.

  self       — "I'm in the web channel", "I'm in this channel" — LLM
               misidentifies Igor's architectural nature (channels are
               transports, not containers).

Input: list of turn dicts from turn_trace_recent (or canned fixtures).
Output: list of Match records.

Usage:
  from lab.claudecode.engram_tools.confab_scanner import ConfabScanner
  scanner = ConfabScanner(current_year=2026)
  matches = scanner.scan(turns)
  for m in matches:
      print(m.turn_id, m.subtype, m.tell_phrase)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from wild_igor.igor.igor_base import IgorBase

# ── tell-phrase libraries ────────────────────────────────────────────────────
#
# Phrases are matched case-insensitively as substrings. Regex patterns are
# used where a shape (like "I'm in the <channel> channel") needs a wildcard.
#
# Confidence is a rough estimate: 1.0 = essentially always a confabulation,
# 0.7 = often a confabulation tell in this context, 0.5 = ambiguous. These
# are starting values; self-learning can tune them once the scanner has
# produced enough review-overridden records (mirroring /review confidence
# tracking).

CAPABILITY_PHRASES: list[tuple[str, float]] = [
    (r"i don'?t have direct access", 0.95),
    (r"i don'?t have access to", 0.85),
    (r"i can'?t (?:actually )?(?:fetch|access|execute|run)", 0.85),
    (r"i'?m just (?:an llm|a language model)", 0.95),
    (r"(?:the hands|the tools) (?:are not|aren'?t) connected", 0.85),
    (r"without (?:fetching|accessing) it", 0.8),
    (r"no autonomous tool-?call loop", 0.8),
]

# Self-confab overlaps with capability in practice. Matched separately so the
# subtype tag tells the engram-engineer which grounding gap to close.
SELF_PHRASES: list[tuple[str, float]] = [
    (r"i'?m in the (?:web|repl|\w+) channel", 0.9),
    (r"from (?:inside |within )?(?:the )?(?:web|repl) channel", 0.85),
    (r"in this channel,? i can'?t", 0.85),
    (r"(?:because|since) i'?m in the", 0.7),
]

# Fact confab detection is shape-based: temporal drift is the big one. Year
# pattern is matched against current_year passed to the scanner.

TEMPORAL_YEAR_PATTERN = re.compile(
    r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})\b",
    re.IGNORECASE,
)
BARE_YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")

VERSION_DRIFT_PHRASES: list[tuple[str, float]] = [
    # Placeholder — add known version-drift phrases as they surface.
    # e.g. (r"python 3\.8", 0.6) if current is 3.12 and context shows Igor
    # should know that. Kept empty in v1 to avoid false positives.
]


@dataclass(frozen=True)
class Match:
    """One confabulation-tell hit.

    turn_id — Igor's turn UUID (from turn_trace_recent)
    subtype — "capability" | "fact" | "self"
    confidence — 0.0–1.0; see phrase-library comments
    tell_phrase — the exact phrase matched (or a descriptor for shape-detections
                  like temporal drift)
    output_preview — first 120 chars of the reply, for quick eyeballing
    """

    turn_id: str
    subtype: str
    confidence: float
    tell_phrase: str
    output_preview: str


class ConfabScanner(IgorBase):
    """Scan turn outputs for confabulation tells.

    current_year: Used for temporal-drift detection. Defaults to today's year
    in UTC. Override in tests for determinism.
    """

    def __init__(self, current_year: Optional[int] = None) -> None:
        super().__init__()
        self.current_year = current_year or datetime.now(timezone.utc).year

    def scan(self, turns: Iterable[dict]) -> list[Match]:
        """Scan a list of turn dicts and return all matches.

        turns: iterable of dicts with at least `turn_id` and `out` (reply text).
               Extra fields (intent, tier, etc.) are ignored by the scanner.
        """
        matches: list[Match] = []
        for turn in turns:
            turn_id = turn.get("turn_id") or turn.get("id") or "<unknown>"
            out = turn.get("out") or turn.get("output") or ""
            if not isinstance(out, str) or not out:
                continue
            preview = out[:120].replace("\n", " ")

            for pattern, conf in CAPABILITY_PHRASES:
                m = re.search(pattern, out, re.IGNORECASE)
                if m:
                    matches.append(
                        Match(turn_id, "capability", conf, m.group(0), preview)
                    )

            for pattern, conf in SELF_PHRASES:
                m = re.search(pattern, out, re.IGNORECASE)
                if m:
                    matches.append(Match(turn_id, "self", conf, m.group(0), preview))

            for pattern, conf in VERSION_DRIFT_PHRASES:
                m = re.search(pattern, out, re.IGNORECASE)
                if m:
                    matches.append(Match(turn_id, "fact", conf, m.group(0), preview))

            fact_match = self._detect_temporal_drift(out)
            if fact_match is not None:
                year_str, year_val = fact_match
                matches.append(
                    Match(
                        turn_id,
                        "fact",
                        0.85,
                        f"temporal drift: {year_str} (current {self.current_year})",
                        preview,
                    )
                )

        return matches

    def _detect_temporal_drift(self, text: str) -> Optional[tuple[str, int]]:
        """Return (matched_string, year) if a past/future year appears that
        diverges from current_year by > 0. None otherwise.

        Only fires on "Month YYYY" patterns to avoid code refs (2024-01-01,
        __init__ line numbers, etc.). Bare-year detection could be added later
        with caller-provided context scoping.
        """
        for match in TEMPORAL_YEAR_PATTERN.finditer(text):
            year = int(match.group(1))
            if year != self.current_year:
                return match.group(0), year
        return None


def scan_turns(
    turns: Iterable[dict], current_year: Optional[int] = None
) -> list[Match]:
    """Convenience: one-call scan without instantiating ConfabScanner."""
    return ConfabScanner(current_year).scan(turns)


# ── CLI entry point ──────────────────────────────────────────────────────────
#
# Live usage requires turn_trace_recent from the Igor MCP server. For
# standalone operation, the caller feeds turns via stdin JSON or a file path.


def _cli(argv: list[str]) -> int:
    import argparse
    import json
    import sys

    ap = argparse.ArgumentParser(
        description="Scan Igor turn outputs for confabulation tells."
    )
    ap.add_argument(
        "--turns-file",
        help="Path to JSON file containing a list of turn dicts. If omitted, reads from stdin.",
    )
    ap.add_argument(
        "--current-year",
        type=int,
        default=None,
        help="Override current year for temporal-drift detection (default: today).",
    )
    args = ap.parse_args(argv)

    if args.turns_file:
        with open(args.turns_file) as f:
            turns = json.load(f)
    else:
        turns = json.load(sys.stdin)

    matches = scan_turns(turns, current_year=args.current_year)
    if not matches:
        print("No confabulation tells detected.")
        return 0

    print(f"{len(matches)} tell(s) detected:\n")
    for m in matches:
        print(f"  [{m.subtype:10s} conf={m.confidence:.2f}] {m.turn_id}")
        print(f"    tell:    {m.tell_phrase}")
        print(f"    preview: {m.output_preview}")
        print()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_cli(sys.argv[1:]))
