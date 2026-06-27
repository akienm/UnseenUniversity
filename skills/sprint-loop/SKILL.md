---
name: sprint-loop
description: Autonomous queue drain — schedules ScheduleWakeup before each batch so compact mid-sprint can't lose the loop. Terminates when queue is empty.
model: sonnet
---

# /sprint-loop — Autonomous queue drain

Runs /sprint-batch in a self-rescheduling loop until the queue is empty.
The wakeup is scheduled *before* the batch starts — this is the key invariant.
If the native compact fires mid-sprint, the scheduled wakeup is already in place and
the loop resumes when CC is next idle.

## Args

Optional selector (same syntax as /sprint-batch):
- `/sprint-loop` — default: today-slate
- `/sprint-loop decision:D-...`
- `/sprint-loop tag:<tag>`
- `/sprint-loop T-x T-y T-z`

## Steps

### 1. Check queue for pending items

```bash
python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/cc_queue.py list 2>/dev/null | grep -E "sprint\]|triage\]" | grep -v "\[akien\]" | grep -v "done"
```

Filter to tickets with status=sprint or triage that are not assigned to akien.
Apply the selector if provided (decision:, tag:, explicit ids).

### 2. If empty — terminate

If no pending items match the selector:
```
queue drained — sprint-loop complete
```
Do NOT schedule a wakeup. The loop ends here.

### 3. If items — schedule wakeup FIRST

Always schedule the wakeup *before* starting the batch. This is the
survivability invariant: if compact fires mid-sprint, the wakeup already
exists and the loop continues.

```python
ScheduleWakeup(
    delaySeconds=90,
    prompt="/sprint-loop [selector]",
    reason="continuing queue drain after batch"
)
```

Pass the same selector through verbatim. If no selector was given, pass
`today-slate` explicitly so the next wakeup uses the same scope.

### 4. Invoke /sprint-batch

```
/sprint-batch [selector]
```

Pass the selector through. /sprint-batch handles everything per-ticket:
claim → build → test → commit → close → savestate. the native compact fires
at batch end.

### 5. Loop

When CC wakes from the scheduled wakeup, it re-enters /sprint-loop at
Step 1. The loop continues until Step 2 terminates it.

## Hard rules

- Schedule the wakeup BEFORE the batch — never after.
- Termination is queue-empty — no other exit condition.
- Selector is passed through unchanged to /sprint-batch and to ScheduleWakeup.
- Never schedule a wakeup on an empty queue — that creates an infinite idle loop.
