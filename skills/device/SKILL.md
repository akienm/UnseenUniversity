---
name: device
description: "Run a device's verb: /device <dev> <verb> [args]. Thin CC-facing shim over `uu device` — resolves the device's skills/ (reasoning, CC runs it) then bin/ (zero-inference, runs via uu device). One dispatch path; the caller never says 'skill' vs 'command'."
model: sonnet
---

# /device — device verb dispatch (CC side)

The CC-facing half of the device two-products split (D-skills-two-products,
T-device-skills-via-uu-device). Each device carries its surface with it:

- `devices/<dev>/skills/<verb>/SKILL.md` — a reasoning-bearing skill CC executes
- `devices/<dev>/bin/<verb>` — a zero-inference executor script

`/device <dev> <verb> [args]` is the ONE entry point. You give a verb; this shim
resolves it. The bare-CLI mirror is `uu device <dev> <verb>` (bin/ only — a
terminal can't run a reasoning skill). Keep the two coherent: every verb runnable
from one is reachable from the other (bin/ from both; skills/ from `/device`).

## Usage

```
/device <dev> <verb> [args]      # e.g. /device igor diagnose
uu device <dev> <verb> [args]    # bare CLI — bin/ verbs only
```

## Steps

### 1. Resolve the verb (skills/ first, then bin/)

`/device` checks the reasoning layer first, then the executor layer — the
failover the ticket specifies. The UNIQUE-NAME rule guarantees a verb is in at
most one of them, so resolution is unambiguous (a name in both is a load error
surfaced by `uu device`).

```bash
DEV="<dev>"; VERB="<verb>"
ROOT="${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devices/$DEV"
if   [ -f "$ROOT/skills/$VERB/SKILL.md" ]; then echo "skill";   # → step 2a
elif [ -x "$ROOT/bin/$VERB" ];            then echo "bin";      # → step 2b
else echo "unknown verb '$VERB' for device '$DEV'"; fi
```

### 2a. skills/ hit — execute the device skill

Read `devices/<dev>/skills/<verb>/SKILL.md` and execute it as you would any
skill: follow its steps, honoring this conversation's args. The skill travels
with the device, so its instructions are device-local.

### 2b. bin/ hit — run the zero-inference executor

Shell out to the CLI (one dispatch path — `/device` does not reimplement bin
dispatch):

```bash
uu device <dev> <verb> [args]
```

### 3. Unknown verb

Report the verb is unknown and list the device's verbs:

```bash
uu device <dev>      # prints the available verbs (errors with the list)
```

## Hard rules

- One dispatch path: bin/ verbs always run via `uu device` (never reimplemented
  here). skills/ verbs are CC-executed.
- The caller gives ONE verb — never asks them to disambiguate skill vs command.
- Verb names are unique per device across bin/+skills/; a collision is a load
  error from `uu device`, not something this shim silently resolves.
