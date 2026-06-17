#!/usr/bin/env python3
"""Debug: examine what Critic sees for each turn."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_UU_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_UU_ROOT))

from devices.dicksimnel.device import DickSimnelDevice
from devices.critic.agent import CriticAgent, Decision

# Load ticket
dsimnel = DickSimnelDevice()
replay = dsimnel.replay_and_analyze("T-error-pattern-test")

print()
print("=" * 80)
print("  DEBUG: What Critic sees for T-error-pattern-test")
print("=" * 80)
print()

critic = CriticAgent()

for turn in replay.get("turns", []):
    print(f"Turn {turn['turn']}: {turn['tool']}")
    print(f"  Decision point: {turn['decision_point']}")
    print(f"  Outcome: {turn['outcome']}")
    print()

    # What does Critic see?
    decision = Decision(
        ticket_id="T-error-pattern-test",
        turn_num=turn["turn"],
        decision_point=turn["decision_point"],
        choice=turn["tool"],
        context={},
        tool_result=turn.get("outcome"),  # This is what gets passed
    )

    print(f"  Decision object created:")
    print(f"    tool_result = {repr(decision.tool_result)}")

    judgment = critic.evaluate_decision(decision)
    print(f"  Verdict: {judgment.verdict} (confidence {judgment.confidence:.2f})")
    if judgment.pattern:
        print(f"  Pattern: {judgment.pattern}")
    print()

print()
print("ISSUE FOUND:")
print("-" * 80)
print("The 'outcome' field (failure/success/neutral) is being passed as tool_result")
print("but Critic expects the actual error/success message text.")
print()
print("SOLUTION:")
print("Need to extract the actual tool_result text from the event logs,")
print("not the summarized 'outcome' field.")
