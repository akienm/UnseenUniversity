#!/usr/bin/env python3
"""
replay_analyzer.py — Analyze closed tickets using DickSimnel's replay simulator.

Loads recorded event logs and steps through builder decisions to understand
why the builder chose specific tool calls and what patterns recur.

Usage:
  python3 replay_analyzer.py <ticket_id>
  python3 replay_analyzer.py <ticket_id> --detail
  python3 replay_analyzer.py --batch <pattern>  # analyze multiple tickets

Output: JSON with event count, decision points, tool call outcomes, success rate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_UU_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_UU_ROOT))

from devices.dicksimnel.device import DickSimnelDevice
from devices.critic.device import CriticDevice


def analyze_single(ticket_id: str, detail: bool = False) -> None:
    """Analyze a single closed ticket."""
    dsimnel = DickSimnelDevice()
    critic = CriticDevice()

    # Get replay data from DickSimnel
    replay_result = dsimnel.replay_and_analyze(ticket_id)
    result = replay_result

    print()
    print(f"═══════════════════════════════════════════════════════════════")
    print(f"  Replay Analysis: {ticket_id}")
    print(f"═══════════════════════════════════════════════════════════════")
    print()

    if "error" in result:
        print(f"❌ Error: {result['error']}")
        print()
        return

    print(f"Events recorded: {result['event_count']}")
    print(f"Tool success rate: {result['success_rate'] * 100:.1f}%")
    print()

    # Get Critic analysis
    critic_analysis = critic.evaluate_replay(ticket_id, result)
    print("CRITIC ANALYSIS:")
    print("-" * 63)
    print(f"  Good/Bad/Neutral: {critic_analysis['verdict_distribution']}")
    if critic_analysis["failure_modes"]:
        print(f"  Failure modes: {', '.join(critic_analysis['failure_modes'])}")
    if critic_analysis["improvement_opportunities"]:
        print(f"  Improvements: {critic_analysis['improvement_opportunities'][0]}")
    print()

    if result["decision_points"]:
        print("DECISION POINTS:")
        print("-" * 63)
        for dp in result["decision_points"]:
            status = "✓" if dp["outcome"] == "success" else "✗" if dp["outcome"] == "failure" else "?"
            print(
                f"  [{status}] Turn {dp['turn']:2d} — {dp['decision']:30s} "
                f"→ {dp['choice']}"
            )
        print()

    if detail and result["turns"]:
        print("TURN SEQUENCE:")
        print("-" * 63)
        for turn in result["turns"][:20]:  # Show first 20 turns
            print(
                f"  Turn {turn['turn']:2d} — {turn['tool']:20s} "
                f"({turn['outcome']})"
            )
        if len(result["turns"]) > 20:
            print(f"  ... ({len(result['turns']) - 20} more turns)")
        print()

    # Output JSON for further analysis
    print("JSON OUTPUT:")
    print("-" * 63)
    print(json.dumps(result, indent=2))
    print()


def main() -> int:
    """Parse args and run analysis."""
    if len(sys.argv) < 2:
        print("Usage: python replay_analyzer.py <ticket_id> [--detail]")
        print("Example: python replay_analyzer.py T-provider-health-classifier")
        return 1

    if sys.argv[1] == "--batch":
        if len(sys.argv) < 3:
            print("Usage: python replay_analyzer.py --batch <pattern>")
            print("Example: python replay_analyzer.py --batch 'T-*'")
            return 1
        print("Batch mode not yet implemented")
        return 1

    ticket_id = sys.argv[1]
    detail = "--detail" in sys.argv

    analyze_single(ticket_id, detail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
