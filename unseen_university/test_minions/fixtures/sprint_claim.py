"""Sprint-claim skill fixture for CC1Minion live tests.

Provides a minimal skill that instructs the model to run cc_queue.py claim,
and the bash patterns expected to appear in the tool calls.
"""

TASK = "Claim ticket T-test-live-minion for processing."

SKILL = """
## Step 2
Always claim the ticket before working on it:
```bash
python3 ${CC_WORKFLOW_TOOLS}/cc_queue.py claim T-test-live-minion
```
"""

EXPECTED_BASH_PATTERNS = [
    "cc_queue.py",
    "T-test-live-minion",
]
