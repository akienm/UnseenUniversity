---
name: recover
description: Rewind safeguard. Call right after a /rewind to re-orient CC against durable state — emits the proven reset message, then reads slate + git to reconcile memory with reality.
model: haiku
---

# /recover — rewind safeguard

A `/rewind` rolls CC's **conversation** back but leaves git, the slate, and the
working tree untouched — so after a rewind CC's memory is *behind* reality. This
skill is the explicit "you were rewound, re-orient now" button. The user knows
orientation was checked because CC ran a named command, not an ad-hoc guess.

This message was verified during the 2026-06-19 rewind test to work perfectly as
a cognitive reset signal:

> **Rewound. Read today's slate and `git log --oneline -10`, reconcile against
> them, and trust slate+git over your memory before doing anything.**

Idempotent — safe to call any number of times; reads only, mutates nothing.

## Steps

### 1. Emit the safeguard message

Output the message above verbatim, so it's visible that the reset signal fired.

### 2. Reconcile against durable state

A rewind can also be a *code* rewind that deletes committed files — so check the
working tree too, not just the conversation lag.

```bash
SLATE=${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/runtime/memory/slates/$(date +%Y%m%d).slate.txt
echo "=== today's slate ($SLATE) ===" && cat "$SLATE" 2>/dev/null || echo "(no slate for today)"
echo "=== git log --oneline -10 ===" && git -C "${UU_ROOT:-$(pwd)}" log --oneline -10
echo "=== git status --short (working-tree divergence) ===" && git -C "${UU_ROOT:-$(pwd)}" status --short
```

### 3. Trust slate + git over memory

Read the slate's `## In-flight` line and the commit log as ground truth. Where
they disagree with what CC "remembers," the slate and git win. If `git status`
shows files unexpectedly deleted (a code-rewind side effect), `git restore <path>`
brings them back. Only after reconciling does CC act.

## Hard rules

- Read-only — never writes the slate, never commits, never mutates state.
- The safeguard message is emitted verbatim every time (the reset signal must be
  recognizable across sessions).
- Reconcile **before** acting on the user's request — the whole point is to not
  build on stale memory.
