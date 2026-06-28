#!/usr/bin/env python3
"""
test_learning_loop.py — Comprehensive testing of the learning loop.

Tests the full observe→criticize→improve pipeline on multiple synthetic tickets.
Reports findings, patterns detected, and improvement effectiveness.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_UU_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_UU_ROOT))

from unseen_university.devices.dicksimnel.device import DickSimnelDevice
from unseen_university.devices.critic.device import CriticDevice
from unseen_university.devices.improver.device import ImproverDevice


def test_ticket(ticket_id: str) -> dict:
    """Run complete learning loop on a ticket and return metrics."""
    dsimnel = DickSimnelDevice()
    critic = CriticDevice()
    improver = ImproverDevice()

    # OBSERVE
    replay = dsimnel.replay_and_analyze(ticket_id)
    if "error" in replay:
        return {"ticket_id": ticket_id, "status": "error", "error": replay["error"]}

    # CRITICIZE
    critique = critic.evaluate_replay(ticket_id, replay)

    # IMPROVE
    learning = improver.learn_from_critic(critique)

    # Compile metrics
    verdict_dist = critique.get("verdict_distribution", {})
    total_decisions = sum(verdict_dist.values())
    good_rate = verdict_dist.get("good", 0) / total_decisions if total_decisions > 0 else 0
    bad_rate = verdict_dist.get("bad", 0) / total_decisions if total_decisions > 0 else 0

    return {
        "ticket_id": ticket_id,
        "status": "success",
        "events_recorded": replay["event_count"],
        "tool_success_rate": replay.get("success_rate", 0),
        "decisions_evaluated": total_decisions,
        "verdict_distribution": verdict_dist,
        "good_decision_rate": good_rate,
        "bad_decision_rate": bad_rate,
        "patterns_found": list(critique.get("common_patterns", {}).keys()),
        "failure_modes": critique.get("failure_modes", []),
        "rules_learned": learning["rules_learned"],
        "improvement_opportunities": critique.get("improvement_opportunities", []),
    }


def main() -> int:
    """Run test suite."""
    print()
    print("=" * 80)
    print("  LEARNING LOOP COMPREHENSIVE TEST SUITE")
    print("=" * 80)
    print()

    test_tickets = [
        "T-test-closed-ticket",
        "T-realistic-test",
        "T-error-pattern-test",
    ]

    results = []
    for ticket_id in test_tickets:
        print(f"Testing {ticket_id}...")
        result = test_ticket(ticket_id)
        results.append(result)
        print(f"  ✓ {result.get('status', 'unknown')}")
        print()

    # ANALYSIS
    print("=" * 80)
    print("  FINDINGS & ANALYSIS")
    print("=" * 80)
    print()

    # Summary stats
    successful = sum(1 for r in results if r.get("status") == "success")
    print(f"Tests run: {len(results)}")
    print(f"Successful: {successful}/{len(results)}")
    print()

    # Decision analysis
    print("DECISION ANALYSIS:")
    print("-" * 80)
    total_decisions = sum(r.get("decisions_evaluated", 0) for r in results)
    total_good = sum(r.get("verdict_distribution", {}).get("good", 0) for r in results)
    total_bad = sum(r.get("verdict_distribution", {}).get("bad", 0) for r in results)
    total_neutral = sum(r.get("verdict_distribution", {}).get("neutral", 0) for r in results)

    print(f"  Total decisions evaluated: {total_decisions}")
    if total_decisions > 0:
        print(f"  Good decisions:     {total_good:3d} ({total_good/total_decisions*100:5.1f}%)")
        print(f"  Bad decisions:      {total_bad:3d} ({total_bad/total_decisions*100:5.1f}%)")
        print(f"  Neutral decisions:  {total_neutral:3d} ({total_neutral/total_decisions*100:5.1f}%)")
    print()

    # Pattern analysis
    print("PATTERNS DETECTED:")
    print("-" * 80)
    all_patterns = {}
    for result in results:
        for pattern in result.get("patterns_found", []):
            all_patterns[pattern] = all_patterns.get(pattern, 0) + 1

    if all_patterns:
        for pattern, count in sorted(all_patterns.items(), key=lambda x: -x[1]):
            print(f"  • {pattern}: found in {count} ticket(s)")
    else:
        print("  (no patterns detected yet)")
    print()

    # Failure mode analysis
    print("FAILURE MODES:")
    print("-" * 80)
    all_failures = {}
    for result in results:
        for mode in result.get("failure_modes", []):
            all_failures[mode] = all_failures.get(mode, 0) + 1

    if all_failures:
        for mode, count in sorted(all_failures.items(), key=lambda x: -x[1]):
            print(f"  • {mode}: observed in {count} ticket(s)")
    else:
        print("  (no failure modes detected)")
    print()

    # Learning effectiveness
    print("LEARNING EFFECTIVENESS:")
    print("-" * 80)
    total_rules = sum(r.get("rules_learned", 0) for r in results)
    print(f"  Total rules learned: {total_rules}")

    all_improvements = {}
    for result in results:
        for opp in result.get("improvement_opportunities", []):
            all_improvements[opp] = all_improvements.get(opp, 0) + 1

    if all_improvements:
        print(f"  Improvement opportunities identified: {len(all_improvements)}")
        for opp, count in sorted(all_improvements.items(), key=lambda x: -x[1])[:5]:
            print(f"    - {opp[:60]}")
    print()

    # Per-ticket details
    print("DETAILED RESULTS:")
    print("-" * 80)
    for result in results:
        if result.get("status") == "error":
            print(f"{result['ticket_id']}: ERROR — {result.get('error')}")
            continue

        print(f"{result['ticket_id']}:")
        print(f"  Events:          {result['events_recorded']}")
        print(f"  Tool success:    {result['tool_success_rate']*100:.1f}%")
        print(f"  Decisions:       {result['decisions_evaluated']}")
        print(f"  Good rate:       {result['good_decision_rate']*100:.1f}%")
        if result["bad_decision_rate"] > 0:
            print(f"  Bad rate:        {result['bad_decision_rate']*100:.1f}%")
        if result["patterns_found"]:
            print(f"  Patterns:        {', '.join(result['patterns_found'])}")
        print(f"  Rules learned:   {result['rules_learned']}")
        print()

    # Conclusions
    print("=" * 80)
    print("  CONCLUSIONS")
    print("=" * 80)
    print()

    if total_bad > 0:
        print(f"✓ Critic successfully identified {total_bad} bad decisions")
        print(f"  → Bad decision detection is working")
    else:
        print(f"⚠ No bad decisions detected (may need to adjust verdict criteria)")

    if total_rules > 0:
        print(f"✓ Improver learned {total_rules} rules from patterns")
        print(f"  → Rule learning is working")
    else:
        print(f"⚠ No rules learned (patterns may need refinement)")

    if all_patterns:
        print(f"✓ Pattern extraction working ({len(all_patterns)} unique patterns)")
        print(f"  → Can identify recurring decision patterns")
    else:
        print(f"⚠ No patterns extracted (may need more diverse test data)")

    print()
    print("NEXT STEPS:")
    if total_bad == 0:
        print("  1. Adjust Critic verdict criteria to identify true failures")
        print("  2. Test with tickets that have explicit error outcomes")
    if len(all_patterns) < 2:
        print("  1. Create test tickets with more varied decision patterns")
        print("  2. Add new pattern detection rules to Critic")
    print("  3. Test on real DickSimnel ticket logs when available")
    print("  4. Measure improvement rate when rules are applied")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
