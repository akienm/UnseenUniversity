#!/usr/bin/env python3
"""
learning_loop.py — Complete observe→criticize→improve learning loop.

Integrates Observer (DickSimnel), Critic, and Improver into one pipeline:
1. Load a closed ticket (observation)
2. Critic evaluates all decisions
3. Improver learns patterns
4. Improver generates recommendations for similar future situations

Usage:
  python3 learning_loop.py <ticket_id>
  python3 learning_loop.py <ticket_id> --apply-rules  # show recommendations
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_UU_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_UU_ROOT))

from devices.dicksimnel.device import DickSimnelDevice
from devices.critic.device import CriticDevice
from devices.improver.device import ImproverDevice


def run_learning_loop(ticket_id: str, apply_rules: bool = False) -> int:
    """Run the complete learning loop."""
    print()
    print("=" * 70)
    print("  OBSERVE → CRITICIZE → IMPROVE Learning Loop")
    print("=" * 70)
    print()

    dsimnel = DickSimnelDevice()
    critic = CriticDevice()
    improver = ImproverDevice()

    # STEP 1: OBSERVE
    print("STEP 1: OBSERVE (DickSimnel replays closed ticket)")
    print("-" * 70)
    replay = dsimnel.replay_and_analyze(ticket_id)
    if "error" in replay:
        print(f"❌ Error: {replay['error']}")
        return 1

    print(f"  Ticket ID: {ticket_id}")
    print(f"  Events recorded: {replay['event_count']}")
    print(f"  Tool success rate: {replay['success_rate'] * 100:.1f}%")
    print()

    # STEP 2: CRITICIZE
    print("STEP 2: CRITICIZE (Critic evaluates decisions)")
    print("-" * 70)
    critique = critic.evaluate_replay(ticket_id, replay)
    print(f"  Verdict distribution: {critique['verdict_distribution']}")
    print(f"  Common patterns: {list(critique['common_patterns'].keys())}")
    if critique["failure_modes"]:
        print(f"  Failure modes: {', '.join(critique['failure_modes'])}")
    print()

    # STEP 3: IMPROVE
    print("STEP 3: IMPROVE (Improver learns from patterns)")
    print("-" * 70)
    learning_result = improver.learn_from_critic(critique)
    print(f"  Rules learned: {learning_result['rules_learned']}")
    for rule in learning_result["rules"][:5]:  # Show first 5
        print(f"    • {rule['pattern']}: {rule['action']}")
    if len(learning_result["rules"]) > 5:
        print(f"    ... and {len(learning_result['rules']) - 5} more rules")
    print()

    # STEP 4: APPLY RULES (optional)
    if apply_rules:
        print("STEP 4: APPLY RULES (recommendations for similar situations)")
        print("-" * 70)
        for turn in replay.get("turns", [])[:3]:  # Show first 3
            context = {
                "decision_point": turn.get("decision_point"),
                "outcome": turn.get("outcome"),
            }
            rec = improver.get_recommendation(context)
            if rec:
                print(
                    f"  Turn {turn['turn']:2d} ({turn['decision_point']}): "
                    f"{rec['action']}"
                )
            else:
                print(f"  Turn {turn['turn']:2d} ({turn['decision_point']}): no rule applies")
        print()

    # SUMMARY
    print("SUMMARY")
    print("-" * 70)
    stats = improver.get_stats()
    print(f"  Observer:  {replay['event_count']} decisions recorded")
    print(f"  Critic:    good={critique['verdict_distribution'].get('good', 0)}, "
          f"bad={critique['verdict_distribution'].get('bad', 0)}")
    print(f"  Improver:  {stats['rules_learned']} rules learned, "
          f"applied {stats['rules_applied']} times")
    if stats["rules_applied"] > 0:
        print(f"  Success rate: {stats['success_rate'] * 100:.1f}%")
    print()

    print("✓ Learning loop complete")
    print("=" * 70)
    print()
    return 0


def main() -> int:
    """Parse args and run learning loop."""
    if len(sys.argv) < 2:
        print("Usage: python learning_loop.py <ticket_id> [--apply-rules]")
        print("Example: python learning_loop.py T-provider-health-classifier")
        return 1

    ticket_id = sys.argv[1]
    apply_rules = "--apply-rules" in sys.argv

    return run_learning_loop(ticket_id, apply_rules)


if __name__ == "__main__":
    sys.exit(main())
