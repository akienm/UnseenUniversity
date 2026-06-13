#!/usr/bin/env python3
"""
simulator_test.py — Test harness for TicketSimulator.

Loads a closed ticket and steps through all turns, showing:
- What tool DickSimnel chose at each turn
- What result was returned (cached)
- Whether the turn succeeded or failed
- Decision points where DickSimnel could have diverged
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add repo root to path
_UU_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_UU_ROOT))

from devices.dicksimnel.simulator import TicketSimulator


def main() -> int:
    """Load a closed ticket and step through all turns."""
    if len(sys.argv) < 2:
        print("Usage: python simulator_test.py <ticket_id>")
        print("Example: python simulator_test.py T-provider-health-classifier")
        return 1

    ticket_id = sys.argv[1]
    sim = TicketSimulator(ticket_id)

    print(f"╔══════════════════════════════════════════════════════════════╗")
    print(f"║ TicketSimulator: {ticket_id:<45} ║")
    print(f"╚══════════════════════════════════════════════════════════════╝")
    print()

    # Show event count
    events = list(sim.replay_all())
    print(f"Total events loaded: {len(events)}")
    print()

    # Replay all turns
    print("TURNS:")
    print("-" * 80)

    for i, event in enumerate(events, 1):
        print(f"Turn {event.turn_num} @ {event.timestamp}")
        print(f"  Decision point: {event.decision_point}")
        print(f"  Tool chosen: {event.tool_name}")
        if event.tool_args:
            print(f"  Tool args: {json.dumps(event.tool_args, indent=4)[:120]}...")
        print(f"  Result: {event.tool_result[:100] if event.tool_result else 'None'}...")
        print(f"  Outcome: {event.outcome}")
        print()

    # Show decision points
    print()
    print("DECISION POINTS:")
    print("-" * 80)
    points = sim.decision_points()
    for point in points:
        status = "✓" if point["outcome"] == "success" else "✗" if point["outcome"] == "failure" else "?"
        print(f"[{status}] Turn {point['turn']:2d} — {point['decision']:30s} → {point['choice']}")

    # Show success rate
    print()
    print("SUCCESS RATE:")
    print("-" * 80)
    rate = sim.success_rate()
    print(f"Tool calls succeeded: {rate * 100:.1f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
