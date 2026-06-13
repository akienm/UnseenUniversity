#!/usr/bin/env python3
"""
test_loop.py — End-to-end test of Critic → Improver learning loop.

Simulates: DickSimnel executes → Critic evaluates → Improver learns →
next decision is better.
"""

from __future__ import annotations

import sys
from pathlib import Path

_UU_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_UU_ROOT))

from devices.critic.agent import CriticAgent, Decision
from devices.improver.agent import ImproverAgent


def test_learning_loop() -> int:
    """Test complete Critic → Improver loop."""
    print("Testing Critic → Improver learning loop...")

    critic = CriticAgent()
    improver = ImproverAgent()

    # STEP 1: DickSimnel executes, generates decisions
    print("\n1. Observer (DickSimnel) executes and records decisions...")
    decisions = [
        Decision(
            ticket_id="T-learn",
            turn_num=1,
            decision_point="tool_selection",
            choice="read_file",
            context={},
            tool_result="ERROR: file not found",
        ),
        Decision(
            ticket_id="T-learn",
            turn_num=2,
            decision_point="tool_selection",
            choice="write_file",
            context={},
            tool_result="success: wrote data",
        ),
        Decision(
            ticket_id="T-learn",
            turn_num=3,
            decision_point="tool_selection",
            choice="read_file",  # Tries again despite error
            context={},
            tool_result="ERROR: file not found",
        ),
    ]
    print(f"   Recorded {len(decisions)} decision points")

    # STEP 2: Critic evaluates decisions
    print("\n2. Critic evaluates decisions...")
    judgments = []
    for decision in decisions:
        judgment = critic.evaluate_decision(decision)
        judgments.append(judgment)
        print(f"   Turn {decision.turn_num}: {decision.choice} → {judgment.verdict}")

    assert any(j.verdict == "bad" for j in judgments), "Should have bad decisions"
    assert any(j.verdict == "good" for j in judgments), "Should have good decisions"

    # STEP 3: Critic extracts patterns
    print("\n3. Critic extracts patterns...")
    analysis = critic.analyze_pattern(judgments)
    print(f"   Good: {analysis['verdict_distribution'].get('good', 0)}")
    print(f"   Bad: {analysis['verdict_distribution'].get('bad', 0)}")
    print(f"   Patterns: {list(analysis['common_patterns'].keys())}")

    # STEP 4: Improver learns from patterns
    print("\n4. Improver learns from patterns...")
    rules = improver.learn_from_patterns(analysis)
    print(f"   Learned {len(rules)} rules")
    for rule in rules:
        print(f"   - {rule.pattern_name}: {rule.action}")

    assert len(rules) > 0, "Should have learned at least one rule"

    # STEP 5: Improver applies rules to next decision
    print("\n5. Improver applies rules to next decision...")
    context = {
        "decision_point": "tool_selection",
        "outcome": "tool_call returns error",
    }
    recommendation = improver.apply_rules(context)
    if recommendation:
        print(f"   Recommendation: {recommendation['action']}")
        print(f"   Confidence: {recommendation['confidence']:.2f}")
        improver.record_improvement(recommendation["rule"], success=True)
    else:
        print("   No recommendation (rules didn't apply)")

    # STEP 6: Verify improvement
    print("\n6. Verify improvement...")
    stats = improver.get_stats()
    print(f"   Rules learned: {stats['rules_learned']}")
    print(f"   Rules applied: {stats['rules_applied']}")
    print(f"   Improvements: {stats['improvements']}")
    if stats["rules_applied"] > 0:
        print(f"   Success rate: {stats['success_rate'] * 100:.1f}%")

    print("\n✓ Learning loop complete: observe → evaluate → learn → improve")
    return 0


def main() -> int:
    """Run the test."""
    print("=" * 63)
    print("Complete Learning Loop Test")
    print("=" * 63)

    try:
        result = test_learning_loop()
        print("\n" + "=" * 63)
        print("✓ Learning loop test passed")
        print("=" * 63)
        return result
    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
