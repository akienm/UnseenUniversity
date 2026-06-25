---
name: available
description: The availability button — reset CC's Granny dispatch availability when done with other work. /available [on|off|status].
model: haiku
---

# /available — Granny availability button

A self-serve button to reset CC's availability with Granny. Use it when you
step away to do something else (an interactive session, a long manual task) and
again when you're ready to rejoin dispatch — without waiting for the next
`/context-load` or a Granny cooldown to expire.

Backed by `devlab/claudecode/cc_available.py`, which manipulates the flag protocol
in `~/.granny/available/` directly (self-contained — runs from any cwd):

- `{worker}.available.true`  — opted in
- `{worker}.available.false` — blocked (`.false` wins over `.true`)
- `{worker}.cooldown_until`  — epoch expiry of a Granny dispatch-timeout cooldown

## Usage

- `/available` or `/available on` — **press the button**: full reset to
  available (clears `.false` AND a stale `cooldown_until`, sets `.true`).
- `/available off` — step away: set `.false` (no cooldown).
- `/available status` — show current state.

Default worker is `CC.0`. Pass a worker id as the last arg to target another.

## Steps

```bash
python3 ${CC_WORKFLOW_TOOLS}/cc_available.py ${1:-on}
```

`${CC_WORKFLOW_TOOLS}` is `${UU_ROOT}/devlab/claudecode`. If unset, use
`$(python3 -c 'from unseen_university._uu_root import uu_root; print(uu_root())')/devlab/claudecode/cc_available.py`.

## When to press it

- **on**: you've finished the interactive/manual work and a sprint-CC is ready
  to consume `cc.0` mailbox dispatches again. Don't press `on` if no CC will
  actually consume dispatches — Granny will route work to CC.0, get no ack, and
  re-cooldown it (benign churn, but pointless). Overnight unattended, leaving
  CC.0 `off` is correct.
- **off**: you're about to do something that should not be interrupted by a
  Granny dispatch.

## Note

`/context-load` Step 0 already presses `on` at session start, and `the native compact`
presses `off` before a compact. This skill is the on-demand version for
mid-session use.
