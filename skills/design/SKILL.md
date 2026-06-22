---
name: design
description: Mark the start of a design-mode block. Optional — /decided can infer scope retroactively without this. Use when you want to explicitly bracket a design session so /decided knows exactly where to start looking back.
model: haiku
---

# /design — Design-mode session marker

Lightweight boundary marker. Design conversations usually don't need this — /decided infers scope from "last N turns since previous /decided or session start." But sometimes you want to say explicitly "ok, from THIS point on we're designing, not building" — that's what /design is for.

## What it does

1. **Writes a DESIGN_START marker to the slate** (## Notes section).
2. **Sets a session tag.** Writes `design_mode: true` to `${IGOR_HOME:-~/.unseen_university}/cc_channel/design_mode.json`.
3. **(Optional nudge on CC's behaviour):** in design mode, bias toward discussion-shape responses — fewer proactive edits, more "what about X?" questions.

## What it does NOT do

- Does not block other commands.
- Does not enforce anything — this is a marker, not a gate.
- Does not auto-close — the block ends at the next /decided or end of day.

## Usage

```
/design
/design <topic or theme>
```

## Steps

1. Compose marker: `DESIGN_START YYYY-MM-DDTHH:MM:SSZ — <topic or (none)>`.
2. Append to today's slate ## Notes:
   ```bash
   echo "- DESIGN_START $(date -Iseconds) — <topic>" >> ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/runtime/memory/slates/$(date +%Y%m%d).slate.txt
   ```
3. Write `design_mode.json`:
   ```bash
   echo '{"design_mode":true,"started_at":"'$(date -Iseconds)'"}' > ${IGOR_HOME:-~/.unseen_university}/cc_channel/design_mode.json
   ```
4. Acknowledge: "Design mode on, scope begins now. Use /decided to close the block and ticketize."

## Ending the block

- **/decided** — ticketizes since DESIGN_START; clears design_mode flag.
- **End of day** — flag ages out.
- **Explicit:** `/design end` — clears the flag without filing tickets.

## Hard rules

- Use /design when the conversation is a design block that will produce multiple decisions.
- DESIGN_START markers are single-firing — re-invoking /design just updates the topic.
