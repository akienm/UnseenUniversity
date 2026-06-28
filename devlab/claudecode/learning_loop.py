#!/usr/bin/env python3
"""
learning_loop.py — Complete observe→criticize→improve learning loop.

The Critic device now handles both evaluation and learning (Improver merged in).

Usage:
  python3 learning_loop.py <ticket_id>
  python3 learning_loop.py <ticket_id> --apply-rules
"""

from __future__ import annotations

import sys
from pathlib import Path

_UU_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_UU_ROOT))

from unseen_university.devices.dicksimnel.device import DickSimnelDevice
from unseen_university.devices.critic.device import CriticDevice


def run_learning_loop(ticket_id: str, apply_rules: bool = False) -> int:
    """Run the complete learning loop."""
    print()
    print("=" * 70)
    print("  OBSERVE → CRITICIZE → IMPROVE Learning Loop")
    print("=" * 70)
    print()

    dsimnel = DickSimnelDevice()
    critic = CriticDevice()

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

    # STEP 3: LEARN (formerly Improver)
    print("STEP 3: LEARN (Critic learns rules from patterns)")
    print("-" * 70)
    learning_result = critic.learn_from_critic(critique)
    print(f"  Rules learned: {learning_result['rules_learned']}")
    for rule in learning_result["rules"][:5]:
        print(f"    • {rule['pattern']}: {rule['action']}")
    if len(learning_result["rules"]) > 5:
        print(f"    ... and {len(learning_result['rules']) - 5} more rules")
    print()

    # STEP 4: APPLY RULES (optional)
    if apply_rules:
        print("STEP 4: APPLY RULES (recommendations for similar situations)")
        print("-" * 70)
        for turn in replay.get("turns", [])[:3]:
            context = {
                "decision_point": turn.get("decision_point"),
                "outcome": turn.get("outcome"),
            }
            rec = critic.get_recommendation(context)
            if rec:
                print(f"  Turn {turn['turn']:2d} ({turn['decision_point']}): {rec['action']}")
            else:
                print(f"  Turn {turn['turn']:2d} ({turn['decision_point']}): no rule applies")
        print()

    # SUMMARY
    print("SUMMARY")
    print("-" * 70)
    stats = critic.get_stats()
    print(f"  Observer:  {replay['event_count']} decisions recorded")
    print(f"  Critic:    good={critique['verdict_distribution'].get('good', 0)}, "
          f"bad={critique['verdict_distribution'].get('bad', 0)}")
    print(f"  Rules:     {stats['rules_learned']} learned, applied {stats['rules_applied']} times")
    if stats["rules_applied"] > 0:
        print(f"  Success rate: {stats['success_rate'] * 100:.1f}%")
    print()

    print("✓ Learning loop complete")
    print("=" * 70)
    print()
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python learning_loop.py <ticket_id> [--apply-rules]")
        return 1
    return run_learning_loop(sys.argv[1], "--apply-rules" in sys.argv)


if __name__ == "__main__":
    sys.exit(main())
