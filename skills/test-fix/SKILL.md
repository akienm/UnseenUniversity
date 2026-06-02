---
name: test-fix
description: Bounded test-run-and-fix loop for this project. Runs tests, fixes failures, retries up to 3 times, then escalates to human. Use when Akien says /test-fix, "run and fix tests", "make the tests pass", or after implementation work completes.
model: sonnet
---

# Test-Fix — Bounded Retry Loop

Run tests → fix failures → repeat. Max 3 passes. Escalate on 3rd failure.

Never brute-force. Never retry the same fix twice. Diagnose before acting.

---

## Pass 1

```bash
cd ~/dev/src/UnseenUniversity && source .venv/bin/activate && python -m pytest tests/ -x -q 2>&1 | tail -30
```

If all pass: done. Report "Tests pass. Ready for `/commit`."

If failures: read each failure carefully. Identify root cause. Fix.

**Allowed fixes on Pass 1:**
- Obvious logic errors
- Import errors from new code
- Missing schema / migration
- Wrong return types

**Not allowed on any pass:**
- Deleting tests to make them pass
- Mocking away the thing being tested
- Skipping tests with `@unittest.skip` without explaining why

---

## Pass 2

Run tests again after fixing Pass 1 failures.

If all pass: done. Report pass count and what was fixed.

If new failures: diagnose. These may be cascade failures from Pass 1 fix. Fix them.

If same failures persist: something deeper is wrong. Investigate — don't re-apply the same fix.

---

## Pass 3

Final attempt. Run tests.

If all pass: done. Report "Tests pass after 3 passes." Note what was fixed in each pass for the commit message.

If still failing: **STOP**. Do not attempt a 4th fix.

---

## Escalation (after Pass 3 failure)

Report to Akien:
```
TEST-FIX ESCALATED after 3 passes.

Remaining failures:
  <test name>: <failure message>

Root cause hypothesis:
  <what I think is wrong and why I can't fix it>

Options:
  1. Investigate <specific thing> — likely 30min work
  2. Skip this test temporarily — reason: <why it might be acceptable>
  3. Revert <specific change> — reason: <why>
```

Do not proceed to commit. Do not continue the work loop. Wait for Akien's direction.

---

## Scope discipline

Only fix what the tests tell you to fix. Do not refactor surrounding code. Do not add features to make tests pass. If a test requires a design change, escalate rather than redesign silently.

---

## If tests run but hang

If pytest hangs (no output after 30s), kill it and report. Do not retry a hanging test suite without understanding why it hung.
