#!/usr/bin/env python3
"""
test_end_to_end.py — End-to-end test of observer→critic→improvement loop.

Simulates a replay session without needing real ticket logs.
Tests: TicketSimulator → Critic evaluation → pattern extraction.
"""

from __future__ import annotations

import sys
from pathlib import Path

_UU_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_UU_ROOT))

from unseen_university.devices.critic.agent import CriticAgent, CriticJudgment, Decision


def test_critic_evaluation() -> int:
    """Test Critic evaluates decisions correctly."""
    print("Testing Critic evaluation...")
    critic = CriticAgent()

    # Simulate a failed decision
    decision = Decision(
        ticket_id="T-test",
        turn_num=1,
        decision_point="tool_selection",
        choice="read_file",
        context={"ticket": "T-test"},
        tool_result="ERROR: file not found",
    )

    judgment = critic.evaluate_decision(decision)
    assert judgment.verdict == "bad", f"Expected 'bad', got '{judgment.verdict}'"
    assert judgment.confidence > 0.8, f"Expected high confidence, got {judgment.confidence}"
    assert "error_not_recovered" in (judgment.pattern or ""), f"Expected error_not_recovered pattern"
    print("✓ Critic correctly identified bad decision")

    # Simulate a good decision
    decision2 = Decision(
        ticket_id="T-test",
        turn_num=2,
        decision_point="tool_selection",
        choice="write_file",
        context={"ticket": "T-test"},
        tool_result="success: wrote 1024 bytes",
    )

    judgment2 = critic.evaluate_decision(decision2)
    assert judgment2.verdict == "good", f"Expected 'good', got '{judgment2.verdict}'"
    assert "successful_forward_progress" in (judgment2.pattern or "")
    print("✓ Critic correctly identified good decision")

    return 0


def test_pattern_analysis() -> int:
    """Test pattern extraction from multiple judgments."""
    print("\nTesting pattern analysis...")
    critic = CriticAgent()

    # Create synthetic judgments
    judgments = []
    for i in range(5):
        verdict = "good" if i < 3 else "bad"
        decision = Decision(
            ticket_id="T-batch",
            turn_num=i + 1,
            decision_point="tool_selection",
            choice="read_file" if verdict == "bad" else "process_data",
            context={},
            tool_result="error" if verdict == "bad" else "success",
        )
        judgment = critic.evaluate_decision(decision)
        judgments.append(judgment)

    # Analyze patterns
    analysis = critic.analyze_pattern(judgments)
    assert analysis["verdict_distribution"]["good"] == 3
    assert analysis["verdict_distribution"]["bad"] == 2
    assert "successful_forward_progress" in analysis["common_patterns"]
    print(f"✓ Pattern analysis: {analysis['verdict_distribution']}")

    return 0


def main() -> int:
    """Run all tests."""
    print("=" * 63)
    print("End-to-End Test: Observer → Critic → Patterns")
    print("=" * 63)

    tests = [
        ("Critic Evaluation", test_critic_evaluation),
        ("Pattern Analysis", test_pattern_analysis),
    ]

    failed = 0
    for name, test in tests:
        try:
            result = test()
            if result == 0:
                print(f"✓ {name}")
            else:
                print(f"✗ {name}: exit code {result}")
                failed += 1
        except Exception as e:
            print(f"✗ {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print()
    print("=" * 63)
    if failed == 0:
        print(f"✓ All tests passed ({len(tests)}/{len(tests)})")
        return 0
    else:
        print(f"✗ {failed}/{len(tests)} tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
