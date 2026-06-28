"""trace_miss_report — given a turn_id, build a structured retrieval-miss report.

Assembles data from three sources (turn trace, memory traces, memory details)
into a single structured report so the engram-engineer can see at a glance:
- what came in
- what cortex pulled
- what didn't come that probably should have (heuristic flags)

The report is data-organization, not magic. Miss-detection heuristics are
shape-based (stale activations, thin retrieval, confabulation tells in output).
The engineer reads the report and decides what engram to deposit next.

Loaders are injected so this is trivially testable without live MCP:
- turn_loader(turn_id) -> dict | None
- trace_loader(since_turn_ts) -> list[dict]
- memory_loader(memory_id) -> dict | None

The default loaders in `live_loaders()` talk to the MCP server via subprocess;
tests use in-memory dict lookups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from unseen_university.devices.igor.igor_base import IgorBase

from .confab_scanner import ConfabScanner, Match

# Age threshold for "stale" activation — memories touched this long ago or
# older are flagged as suspect anchors for a current query.
STALE_ACTIVATION_DAYS = 14


@dataclass
class ActivatedMemory:
    """One memory node that fired during this turn's cortex search."""

    memory_id: str
    memory_type: str
    narrative_preview: str
    relevance: float
    deposited_at: Optional[str]  # ISO string or None
    age_days: Optional[int]  # computed vs report generation time


@dataclass
class TraceMissReport:
    """Structured retrieval-miss analysis.

    turn_id — the turn being analyzed
    input_preview — first ~200 chars of what came in
    output_preview — first ~200 chars of what went out
    intent — thalamus intent classification
    bg_top_habit — basal ganglia winning habit (or None if fallthrough)
    tier — reasoning tier used (tier.2, tier.3+, etc.)
    cortex_queries — list of search queries issued during this turn
    activated_memories — memories that fired, flattened across queries
    confab_matches — confabulation tells detected in the output
    miss_flags — heuristic warnings about likely grounding gaps
    suggested_engram_shape — free-text suggestion for what memory to deposit
    """

    turn_id: str
    input_preview: str
    output_preview: str
    intent: Optional[str]
    bg_top_habit: Optional[str]
    tier: Optional[str]
    cortex_queries: list[str]
    activated_memories: list[ActivatedMemory]
    confab_matches: list[Match]
    miss_flags: list[str]
    suggested_engram_shape: Optional[str] = None


class TraceMissAnalyzer(IgorBase):
    """Build TraceMissReport from injected loaders.

    current_year: Passed to ConfabScanner for temporal-drift detection.
    now: Report-generation time, used for age computation. Defaults to
         datetime.now(UTC); override in tests for determinism.
    """

    def __init__(
        self,
        turn_loader: Callable[[str], Optional[dict]],
        trace_loader: Callable[[str], list[dict]],
        memory_loader: Callable[[str], Optional[dict]],
        current_year: Optional[int] = None,
        now: Optional[datetime] = None,
    ) -> None:
        super().__init__()
        self.turn_loader = turn_loader
        self.trace_loader = trace_loader
        self.memory_loader = memory_loader
        self.now = now or datetime.now(timezone.utc)
        self.scanner = ConfabScanner(current_year=current_year or self.now.year)

    def analyze(self, turn_id: str) -> Optional[TraceMissReport]:
        """Return a TraceMissReport for turn_id, or None if turn not found."""
        turn = self.turn_loader(turn_id)
        if not turn:
            return None

        turn_ts = turn.get("timestamp") or turn.get("ts")
        input_text = turn.get("in") or turn.get("input") or ""
        output_text = turn.get("out") or turn.get("output") or ""

        # 1. Confabulation scan on the output
        confab_matches = self.scanner.scan([turn])

        # 2. Memory traces: pull traces near this turn timestamp
        traces = self.trace_loader(turn_ts or "") if turn_ts else []

        cortex_queries: list[str] = []
        activated: list[ActivatedMemory] = []
        for trace in traces:
            q = trace.get("query") or trace.get("q") or ""
            if q:
                cortex_queries.append(q)
            for node in trace.get("top_nodes") or []:
                mem = self.memory_loader(node.get("memory_id") or "")
                if mem is None:
                    continue
                activated.append(self._build_activated(mem, node))

        # 3. Heuristic miss flags
        miss_flags = self._compute_miss_flags(
            intent=turn.get("intent"),
            output_text=output_text,
            confab_matches=confab_matches,
            activated=activated,
            cortex_queries=cortex_queries,
        )

        # 4. Suggested engram shape (free-text, only when we have a strong
        # signal about what kind of gap fired)
        suggestion = self._suggest_engram(confab_matches, miss_flags)

        return TraceMissReport(
            turn_id=turn_id,
            input_preview=input_text[:200].replace("\n", " "),
            output_preview=output_text[:200].replace("\n", " "),
            intent=turn.get("intent"),
            bg_top_habit=turn.get("bg") or turn.get("bg_top_habit"),
            tier=turn.get("tier"),
            cortex_queries=cortex_queries,
            activated_memories=activated,
            confab_matches=confab_matches,
            miss_flags=miss_flags,
            suggested_engram_shape=suggestion,
        )

    def _build_activated(self, mem: dict, node: dict) -> ActivatedMemory:
        deposited = (
            mem.get("metadata", {}).get("deposited_at")
            if isinstance(mem.get("metadata"), dict)
            else mem.get("deposited_at")
        )
        age_days = self._age_days(deposited)
        return ActivatedMemory(
            memory_id=mem.get("id", node.get("memory_id", "?")),
            memory_type=mem.get("memory_type", "?"),
            narrative_preview=(mem.get("narrative") or "")[:120].replace("\n", " "),
            relevance=float(node.get("relevance", 0.0)),
            deposited_at=deposited,
            age_days=age_days,
        )

    def _age_days(self, deposited_at: Optional[str]) -> Optional[int]:
        if not deposited_at:
            return None
        try:
            dep = datetime.fromisoformat(deposited_at.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dep.tzinfo is None:
            dep = dep.replace(tzinfo=timezone.utc)
        return max(0, (self.now - dep).days)

    def _compute_miss_flags(
        self,
        intent: Optional[str],
        output_text: str,
        confab_matches: list[Match],
        activated: list[ActivatedMemory],
        cortex_queries: list[str],
    ) -> list[str]:
        flags: list[str] = []

        if confab_matches:
            subtypes = {m.subtype for m in confab_matches}
            flags.append(
                f"confabulation tells detected (subtypes: {', '.join(sorted(subtypes))})"
            )

        if activated:
            stale = [m for m in activated if (m.age_days or 0) >= STALE_ACTIVATION_DAYS]
            if stale and len(stale) == len(activated):
                flags.append(
                    f"all {len(activated)} activated memories are stale (≥{STALE_ACTIVATION_DAYS}d old) — no fresh anchor for this query"
                )
            elif stale:
                flags.append(
                    f"{len(stale)}/{len(activated)} activated memories are stale (≥{STALE_ACTIVATION_DAYS}d old)"
                )

        if cortex_queries and not activated:
            flags.append(
                f"cortex ran {len(cortex_queries)} queries but no memories surfaced — retrieval empty"
            )

        if intent in {"general", "complaint"} and confab_matches:
            flags.append(
                f"intent={intent} combined with confabulation tells — LLM filled with priors, not grounded memory"
            )

        factual_count = sum(1 for m in activated if m.memory_type == "FACTUAL")
        if confab_matches and factual_count == 0:
            flags.append(
                "no FACTUAL memories activated during a confabulated turn — grounding-anchor gap"
            )

        return flags

    def _suggest_engram(
        self, confab_matches: list[Match], miss_flags: list[str]
    ) -> Optional[str]:
        if not confab_matches:
            return None
        subtypes = {m.subtype for m in confab_matches}
        if "capability" in subtypes or "self" in subtypes:
            return (
                "Deposit a FACTUAL engram anchoring Igor's actual capability/self model "
                "for this query space (channels are transports, tools are channel-agnostic, "
                "anchor keywords for the domain the LLM confabulated over)."
            )
        if "fact" in subtypes:
            return (
                "Deposit a FACTUAL engram with the current-state fact (e.g. current date, "
                "current version) anchored by likely query keywords."
            )
        return None


# ── CLI + default loaders ────────────────────────────────────────────────────


def render_report(report: TraceMissReport) -> str:
    """Human-readable rendering of a TraceMissReport for stdout/logs."""
    lines: list[str] = []
    lines.append(f"Trace-miss report — turn {report.turn_id}")
    lines.append(
        f"  intent: {report.intent}  bg: {report.bg_top_habit}  tier: {report.tier}"
    )
    lines.append(f"  in:  {report.input_preview}")
    lines.append(f"  out: {report.output_preview}")
    lines.append("")

    lines.append(f"  cortex queries ({len(report.cortex_queries)}):")
    for q in report.cortex_queries:
        lines.append(f"    - {q}")

    lines.append(f"  activated memories ({len(report.activated_memories)}):")
    for m in report.activated_memories:
        age = f"{m.age_days}d" if m.age_days is not None else "?d"
        lines.append(
            f"    - [{m.memory_type} rel={m.relevance:.2f} age={age}] {m.memory_id}: {m.narrative_preview}"
        )

    lines.append(f"  confabulation tells ({len(report.confab_matches)}):")
    for c in report.confab_matches:
        lines.append(f"    - [{c.subtype} conf={c.confidence:.2f}] {c.tell_phrase}")

    lines.append(f"  miss flags ({len(report.miss_flags)}):")
    for f in report.miss_flags:
        lines.append(f"    ! {f}")

    if report.suggested_engram_shape:
        lines.append(f"  suggested engram: {report.suggested_engram_shape}")

    return "\n".join(lines)


def _cli(argv: list[str]) -> int:
    """Stub CLI. Live loaders that hit the MCP server come in a follow-up
    ticket — this v1 expects the caller to pipe canned data in tests."""
    import argparse

    ap = argparse.ArgumentParser(
        description="Build a retrieval-miss report for a given turn_id.",
    )
    ap.add_argument("turn_id", help="Turn UUID to analyze")
    ap.add_argument(
        "--fixture",
        help="Path to JSON fixture for offline runs (default: require live loaders)",
    )
    args = ap.parse_args(argv)

    if not args.fixture:
        print(
            "Live loaders not wired in v1. Pass --fixture <path> with "
            "{turn, traces, memories} for offline use."
        )
        return 2

    import json

    with open(args.fixture) as f:
        data = json.load(f)

    turns_by_id = {t["turn_id"]: t for t in data.get("turns", [])}
    traces_by_ts = data.get("traces_by_ts", {})
    mems_by_id = {m["id"]: m for m in data.get("memories", [])}

    analyzer = TraceMissAnalyzer(
        turn_loader=lambda tid: turns_by_id.get(tid),
        trace_loader=lambda ts: traces_by_ts.get(ts, []),
        memory_loader=lambda mid: mems_by_id.get(mid),
    )

    report = analyzer.analyze(args.turn_id)
    if report is None:
        print(f"turn not found: {args.turn_id}")
        return 1
    print(render_report(report))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_cli(sys.argv[1:]))
