"""
simulator.py — Execution sandbox for builder step-tracing via event sourcing and replay.

TicketSimulator replays closed tickets step-by-step without live inference.
Enables observing why DickSimnel makes decisions. Foundation for Critic training
and pattern mining.

Immutable event stream: (timestamp, turn, decision_point, tool_call, result, outcome)
sourced from datacenter_logs/<ticket>/.

Usage:
  sim = TicketSimulator(ticket_id="T-provider-health-classifier")
  for turn in sim.replay_all():
      # Single-step: observe what tool DickSimnel chose, why
      # Answer tool calls from cache or escalate to CC shim
      tool_result = sim.answer_tool_call(turn.tool_name, turn.tool_args)
      sim.record_outcome(turn.turn_num, tool_result, success=True)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

_LOGS_ROOT = Path.home() / ".unseen_university" / "Igor-wild-0001" / "datacenter_logs" / "inference"


@dataclass
class Event:
    """Immutable event in builder execution trace."""
    timestamp: str
    turn_num: int
    decision_point: str  # "tool_selection", "param_choice", etc
    tool_name: Optional[str]
    tool_args: Optional[dict]
    tool_result: Optional[str]
    outcome: Optional[str]  # "success", "failure", "escalate"


class TicketSimulator:
    """Replay closed tickets for builder step-tracing."""

    def __init__(self, ticket_id: str) -> None:
        self._ticket_id = ticket_id
        self._log_dir = _LOGS_ROOT / ticket_id
        self._events: list[Event] = []
        self._turn_index = 0
        self._load_events()

    def _load_events(self) -> None:
        """Load immutable event stream from datacenter logs."""
        if not self._log_dir.exists():
            log.warning("simulator: log dir not found: %s", self._log_dir)
            return

        # Load all turns from ticket execution log
        for log_file in sorted(self._log_dir.glob("turn_*.jsonl")):
            try:
                with open(log_file) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        event = Event(
                            timestamp=data.get("timestamp", ""),
                            turn_num=data.get("turn", 0),
                            decision_point=data.get("decision_point", ""),
                            tool_name=data.get("tool_name"),
                            tool_args=data.get("tool_args", {}),
                            tool_result=data.get("tool_result"),
                            outcome=data.get("outcome"),
                        )
                        self._events.append(event)
            except Exception as e:
                log.warning("simulator: failed to load %s: %s", log_file, e)

        log.info("simulator: loaded %d events for %s", len(self._events), self._ticket_id)

    def replay_all(self):
        """Iterate through all turns in execution order."""
        for event in self._events:
            yield event

    def answer_tool_call(
        self, tool_name: str, tool_args: dict, use_cache: bool = True
    ) -> str:
        """Answer a tool call using cached data or CC shim escalation.

        Args:
            tool_name: Bash, Read, Edit, Write
            tool_args: tool parameters
            use_cache: prefer cached results from prior run

        Returns:
            tool result (string)
        """
        # Look for cached result in current event stream
        if use_cache:
            for event in self._events:
                if (
                    event.tool_name == tool_name
                    and event.tool_args == tool_args
                    and event.tool_result
                ):
                    log.debug(
                        "simulator: cache hit for %s(%s)", tool_name, list(tool_args.keys())
                    )
                    return event.tool_result

        # No cache — escalate to CC shim for live execution
        log.info("simulator: escalating to CC shim: %s", tool_name)
        return f"[CC SHIM] {tool_name}: cached result not found"

    def record_outcome(self, turn_num: int, result: str, success: bool) -> None:
        """Record the outcome of a tool call for analysis."""
        for event in self._events:
            if event.turn_num == turn_num:
                event.outcome = "success" if success else "failure"
                event.tool_result = result
                log.debug("simulator: recorded outcome for turn %d: %s", turn_num, event.outcome)
                break

    def decision_points(self) -> list[dict]:
        """Extract all decision points from trace (where builder could diverge)."""
        points = []
        for event in self._events:
            if event.decision_point:
                points.append(
                    {
                        "turn": event.turn_num,
                        "decision": event.decision_point,
                        "choice": event.tool_name,
                        "outcome": event.outcome,
                    }
                )
        return points

    def success_rate(self) -> float:
        """Compute success rate of tool calls in trace."""
        if not self._events:
            return 0.0
        successes = sum(1 for e in self._events if e.outcome == "success")
        return successes / len(self._events) if self._events else 0.0
