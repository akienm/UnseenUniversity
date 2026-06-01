"""
routing_compiler.py — Scraps job: analyze escalation corpus, surface compile-down proposals.

Reads ~/.granny/escalation_corpus.jsonl and finds three pattern types:

  UPGRADE_TIER   — (tag, size) where UPGRADE advisor signal is frequent →
                   propose routing that (tag, size) directly to a higher tier,
                   skipping the worker→escalate→analyst round-trip.

  SETUP_GAP      — BLOCKED advisor signals where excerpt keywords indicate a
                   connectivity / setup issue (ECONNREFUSED, ConnectionRefused,
                   socket, timeout) → flag as infrastructure gap, not model gap.

  REPROMPT_RATE  — tags where REPROMPT rate exceeds threshold → flag the
                   ticket template for that tag as needing improvement.

Proposals at or above the auto-apply threshold (>90% confidence, >20 samples)
are written to ~/.granny/compiled_routing_rules.json for use by
inference_dispatch_fn. The live wiring is gated pending explicit approval —
this job surfaces proposals to channel; auto-apply is an opt-in step.

Run: python -m devices.scraps.jobs.routing_compiler
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT_CORPUS = (
    Path(os.environ.get("GRANNY_HOME", str(Path.home() / ".granny")))
    / "escalation_corpus.jsonl"
)
_DEFAULT_COMPILED_RULES = (
    Path(os.environ.get("GRANNY_HOME", str(Path.home() / ".granny")))
    / "compiled_routing_rules.json"
)

# Thresholds for auto-apply. Only proposals with both confidence AND sample_count
# at or above these values are auto-applied.
AUTO_APPLY_CONFIDENCE = float(os.environ.get("COMPILER_AUTO_APPLY_CONFIDENCE", "0.90"))
AUTO_APPLY_MIN_SAMPLES = int(os.environ.get("COMPILER_AUTO_APPLY_MIN_SAMPLES", "20"))

# REPROMPT rate (fraction) above which a template-improvement proposal fires.
REPROMPT_THRESHOLD = float(os.environ.get("COMPILER_REPROMPT_THRESHOLD", "0.40"))

# Keyword fragments that identify BLOCKED signals as infrastructure gaps.
_SETUP_GAP_KEYWORDS = (
    "econnrefused",
    "connectionrefused",
    "connection refused",
    "no route to host",
    "socket",
    "timeout",
    "connection timed out",
    "network is unreachable",
)

# Tier escalation map: when UPGRADE fires after a worker attempt, the next tier is analyst.
_NEXT_TIER = {"worker": "analyst", "analyst": "designer"}


@dataclass
class CompileDownProposal:
    """A proposed routing compile-down, surfaced by the compiler."""

    kind: str  # "UPGRADE_TIER" | "SETUP_GAP" | "REPROMPT_RATE"
    tag: str
    size: str
    task_class: str
    confidence: float  # fraction 0.0–1.0
    sample_count: int
    detail: str
    proposed_task_class: str | None = None  # UPGRADE_TIER only

    @property
    def auto_apply_eligible(self) -> bool:
        return (
            self.confidence >= AUTO_APPLY_CONFIDENCE
            and self.sample_count >= AUTO_APPLY_MIN_SAMPLES
        )

    def channel_line(self) -> str:
        tier_label = (
            f"|proposed_tier={self.proposed_task_class}"
            if self.proposed_task_class
            else ""
        )
        auto = "|AUTO" if self.auto_apply_eligible else ""
        return (
            f"  [{self.kind}] {self.tag}/{self.size} task={self.task_class}"
            f" conf={self.confidence:.0%} n={self.sample_count}"
            f"{tier_label}{auto}: {self.detail}"
        )


class RoutingCompiler:
    """
    Reads the escalation corpus and derives compile-down proposals.

    Three pattern detectors (all run on every call to analyze()):
      1. UPGRADE_TIER  — frequent UPGRADE advisor signals by (tag, size, task_class)
      2. SETUP_GAP     — BLOCKED signals containing infrastructure-error keywords
      3. REPROMPT_RATE — high REPROMPT rate by tag
    """

    def __init__(
        self,
        corpus_path: Path | None = None,
        compiled_rules_path: Path | None = None,
        reprompt_threshold: float = REPROMPT_THRESHOLD,
        auto_apply_confidence: float = AUTO_APPLY_CONFIDENCE,
        auto_apply_min_samples: int = AUTO_APPLY_MIN_SAMPLES,
    ) -> None:
        self._corpus_path = corpus_path or _DEFAULT_CORPUS
        self._compiled_rules_path = compiled_rules_path or _DEFAULT_COMPILED_RULES
        self._reprompt_threshold = reprompt_threshold
        self._auto_apply_confidence = auto_apply_confidence
        self._auto_apply_min_samples = auto_apply_min_samples

    # ── Corpus I/O ────────────────────────────────────────────────────────────

    def load_corpus(self) -> list[dict]:
        """Read all entries from the escalation corpus JSONL. Returns [] on missing file."""
        if not self._corpus_path.exists():
            return []
        entries: list[dict] = []
        with self._corpus_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError as e:
                    log.warning("routing_compiler: bad corpus line — %s", e)
        log.info("routing_compiler: loaded %d corpus entries", len(entries))
        return entries

    # ── Pattern detectors ─────────────────────────────────────────────────────

    def _detect_upgrade_tier(self, entries: list[dict]) -> list[CompileDownProposal]:
        """UPGRADE_TIER: (tag, size, task_class) buckets with high UPGRADE advisor rate."""
        # bucket: (tag, size, task_class) → {total_escalations, upgrade_count}
        buckets: dict[tuple, dict] = defaultdict(lambda: {"total": 0, "upgrade": 0})

        for e in entries:
            advisor = (e.get("advisor_signal") or "").upper()
            signal = e.get("signal", "")
            if not signal.startswith("ESCALATE"):
                continue
            task_class = e.get("task_class", "worker")
            size = e.get("size", "?")
            for tag in e.get("tags", []):
                key = (tag, size, task_class)
                buckets[key]["total"] += 1
                if advisor == "UPGRADE":
                    buckets[key]["upgrade"] += 1

        proposals = []
        for (tag, size, task_class), b in buckets.items():
            total = b["total"]
            upgrade = b["upgrade"]
            if total < 3:  # too few samples to propose anything
                continue
            confidence = upgrade / total
            if confidence < 0.5:
                continue
            next_tier = _NEXT_TIER.get(task_class)
            if next_tier is None:
                continue
            proposals.append(
                CompileDownProposal(
                    kind="UPGRADE_TIER",
                    tag=tag,
                    size=size,
                    task_class=task_class,
                    confidence=round(confidence, 3),
                    sample_count=total,
                    detail=(
                        f"{upgrade}/{total} escalations are UPGRADE — "
                        f"route {tag}/{size} directly to {next_tier}"
                    ),
                    proposed_task_class=next_tier,
                )
            )
        return proposals

    def _detect_setup_gap(self, entries: list[dict]) -> list[CompileDownProposal]:
        """SETUP_GAP: BLOCKED signals with infrastructure-error keywords in excerpt."""
        tag_buckets: dict[str, dict] = defaultdict(lambda: {"blocked": 0, "keyword": 0})

        for e in entries:
            advisor = (e.get("advisor_signal") or "").upper()
            if advisor != "BLOCKED":
                continue
            excerpt = (e.get("excerpt") or "").lower()
            is_setup = any(kw in excerpt for kw in _SETUP_GAP_KEYWORDS)
            for tag in e.get("tags", []):
                tag_buckets[tag]["blocked"] += 1
                if is_setup:
                    tag_buckets[tag]["keyword"] += 1

        proposals = []
        for tag, b in tag_buckets.items():
            blocked = b["blocked"]
            keyword = b["keyword"]
            if blocked < 2:
                continue
            confidence = keyword / blocked
            if confidence < 0.5:
                continue
            proposals.append(
                CompileDownProposal(
                    kind="SETUP_GAP",
                    tag=tag,
                    size="*",
                    task_class="*",
                    confidence=round(confidence, 3),
                    sample_count=blocked,
                    detail=(
                        f"{keyword}/{blocked} BLOCKED signals contain setup-error "
                        f"keywords — likely infrastructure gap, not model gap"
                    ),
                )
            )
        return proposals

    def _detect_reprompt_rate(self, entries: list[dict]) -> list[CompileDownProposal]:
        """REPROMPT_RATE: tags with high REPROMPT advisor rate → template improvement needed."""
        tag_buckets: dict[str, dict] = defaultdict(lambda: {"total": 0, "reprompt": 0})

        for e in entries:
            advisor = (e.get("advisor_signal") or "").upper()
            for tag in e.get("tags", []):
                tag_buckets[tag]["total"] += 1
                if advisor == "REPROMPT":
                    tag_buckets[tag]["reprompt"] += 1

        proposals = []
        for tag, b in tag_buckets.items():
            total = b["total"]
            reprompt = b["reprompt"]
            if total < 3:
                continue
            rate = reprompt / total
            if rate < self._reprompt_threshold:
                continue
            proposals.append(
                CompileDownProposal(
                    kind="REPROMPT_RATE",
                    tag=tag,
                    size="*",
                    task_class="*",
                    confidence=round(rate, 3),
                    sample_count=total,
                    detail=(
                        f"{reprompt}/{total} dispatches triggered REPROMPT "
                        f"({rate:.0%} > {self._reprompt_threshold:.0%} threshold) — "
                        f"ticket template for '{tag}' needs improvement"
                    ),
                )
            )
        return proposals

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(self, entries: list[dict]) -> list[CompileDownProposal]:
        """Run all three detectors on the given corpus entries. Returns merged proposals."""
        proposals = (
            self._detect_upgrade_tier(entries)
            + self._detect_setup_gap(entries)
            + self._detect_reprompt_rate(entries)
        )
        proposals.sort(key=lambda p: (-p.confidence, -p.sample_count))
        log.info(
            "routing_compiler: %d proposals from %d entries",
            len(proposals),
            len(entries),
        )
        return proposals

    def apply_proposal(
        self,
        proposal: CompileDownProposal,
        compiled_rules_path: Path | None = None,
    ) -> None:
        """Write a compile-down rule for a high-confidence UPGRADE_TIER proposal.

        Appends to the compiled_routing_rules.json sidecar that
        inference_dispatch_fn will consult when auto-apply is enabled.

        Auto-apply is currently gated — this method exists for testing and
        for future CC-triggered application after Akien review.
        """
        if proposal.kind != "UPGRADE_TIER" or not proposal.proposed_task_class:
            log.debug(
                "apply_proposal: skipping non-UPGRADE_TIER proposal %s", proposal.kind
            )
            return

        path = compiled_rules_path or self._compiled_rules_path
        path.parent.mkdir(parents=True, exist_ok=True)

        try:
            existing: dict = json.loads(path.read_text()) if path.exists() else {}
        except (json.JSONDecodeError, OSError):
            existing = {}

        rule_key = f"{proposal.tag}/{proposal.size}"
        existing[rule_key] = {
            "from_task_class": proposal.task_class,
            "to_task_class": proposal.proposed_task_class,
            "confidence": proposal.confidence,
            "sample_count": proposal.sample_count,
            "detail": proposal.detail,
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(existing, indent=2))
        log.info(
            "routing_compiler: compiled rule written %s → %s (conf=%.0f%%, n=%d) at %s",
            rule_key,
            proposal.proposed_task_class,
            proposal.confidence * 100,
            proposal.sample_count,
            path,
        )

    def post_proposals(self, proposals: list[CompileDownProposal]) -> None:
        """Post COMPILE_DOWN_PROPOSALS to the shared channel."""
        if not proposals:
            return
        lines = [f"COMPILE_DOWN_PROPOSALS|count={len(proposals)}"]
        for p in proposals:
            lines.append(p.channel_line())
        msg = "\n".join(lines)
        try:
            from unseen_university.channel import post_to_channel

            post_to_channel(msg, author="scraps-routing-compiler", channel="shared")
            log.info("routing_compiler: posted %d proposals to channel", len(proposals))
        except Exception as e:
            log.warning("routing_compiler: channel post failed: %s", e)

    def run(self) -> list[CompileDownProposal]:
        """Main entry point: load corpus → analyze → surface proposals → auto-apply eligible.

        Auto-apply fires only for proposals at or above both thresholds
        (confidence >= AUTO_APPLY_CONFIDENCE and sample_count >= AUTO_APPLY_MIN_SAMPLES).
        Live auto-apply is gated — wiring to production requires explicit approval.
        """
        entries = self.load_corpus()
        if not entries:
            log.info("routing_compiler: corpus empty — nothing to analyze")
            return []

        proposals = self.analyze(entries)
        self.post_proposals(proposals)

        for proposal in proposals:
            if proposal.auto_apply_eligible:
                log.info(
                    "routing_compiler: auto-apply candidate %s/%s conf=%.0f%% n=%d"
                    " — GATED pending approval; call apply_proposal() explicitly to apply",
                    proposal.tag,
                    proposal.size,
                    proposal.confidence * 100,
                    proposal.sample_count,
                )

        return proposals


if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    compiler = RoutingCompiler()
    proposals = compiler.run()
    print(f"Proposals: {len(proposals)}")
    for p in proposals:
        print(p.channel_line())
    sys.exit(0)
