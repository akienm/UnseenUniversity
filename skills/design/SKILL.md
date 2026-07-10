---
name: design
description: Open a design block — capture the intention it realizes as a first-class DRAFT design artifact, and mark scope so /sorted knows where to look back. /sorted promotes the draft into the resolved design.
model: haiku
---

# /design — Open a design block (emit the draft design)

The opening mark of a design conversation. Two jobs: (1) bracket scope so `/sorted`
knows where the block begins, and (2) **capture the intention this block realizes
as a first-class DRAFT design artifact** — the front edge of the settled
`INTENTION -> DESIGN -> TICKET` stack (architecture/workflow-levels;
T-design-first-artifact-type). The front boundary used to have nothing durable;
the draft is where the intention starts accreting.

`/sorted` closes the block and **promotes the same design** (reusing its id +
stamp — an atomic overwrite) into the resolved design: forks filled, tickets
spawned, back-compat decision projected.

## What it does

1. **Captures the intention** the block realizes (the "I intend that…" statement).
   Ask for it if not obvious from the arg — it's the one field a draft needs.
2. **Emits a DRAFT design** to `devlab/runtime/memory/designs/` via `design_emit.py
   --draft` (relaxed contract: `design_id` + `intentions` only; no forks yet, no
   projected decision — a draft is not a resolved claim).
3. **Writes the design_mode flag** carrying the draft's `design_id` + `stamp` so
   `/sorted` can promote the SAME artifact instead of minting a second one.
4. **Writes a DESIGN_START marker to the slate** (## Notes) for scope look-back.

## Usage

```
/design <intention or topic>
/design                       # will ask for the intention it realizes
```

## Steps

1. **Get the intention.** From the arg, or ask: "Which intention does this block
   realize? (the 'I intend that…' statement)". Mint `design_id =
   Design-<kebab-slug>-YYYY-MM-DD` and a `stamp` (yyyymmdd.hhmmssuuuuuu).

2. **Emit the draft design:**
   ```bash
   cat > /tmp/draft_design.json <<'JSON'
   {
     "design_id": "Design-<slug>-YYYY-MM-DD",
     "status": "draft",
     "date": "YYYY-MM-DD",
     "intentions": ["<the 'I intend that…' statement>"],
     "text": "# Design-<slug> (draft)\nIntention: <statement>\nOpened <ISO8601>."
   }
   JSON
   STAMP=$(python3 -c "from datetime import datetime; print(datetime.now().strftime('%Y%m%d.%H%M%S%f'))")
   python3 "${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/design_emit.py" \
     --draft --stamp "$STAMP" --body-file /tmp/draft_design.json \
     --produced-by "<intent:I-* if named, else session:cc.0>"
   ```

3. **Write `design_mode.json`** (carries the id + stamp so /sorted promotes, not duplicates):
   ```bash
   echo '{"design_mode":true,"started_at":"'$(date -Iseconds)'","design_id":"Design-<slug>-YYYY-MM-DD","stamp":"'$STAMP'"}' \
     > $HOME/.unseen_university/cc_channel/design_mode.json
   ```

4. **Append the slate marker:**
   ```bash
   echo "- DESIGN_START $(date -Iseconds) — Design-<slug> — <intention>" \
     >> ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/runtime/memory/slates/$(date +%Y%m%d).slate.txt
   ```

5. Acknowledge: "Design mode on — draft `Design-<slug>` opened for `<intention>`.
   Use /sorted to resolve the forks, spawn tickets, and promote it."

## Ending the block

- **/sorted** — reads `design_mode.json`, reuses the draft's `design_id` + `stamp`,
  fills forks + spawned_tickets, re-emits at `draft=False` (full contract +
  back-compat decision projection), clears the flag.
- **End of day** — flag ages out; the draft remains in `designs/` as a captured
  intention (a design that never resolved is a visible open intention, not lost).
- **Explicit:** `/design end` — clears the flag without promoting.

## What it does NOT do

- Does not block other commands or gate anything — the draft is a capture, not a wall.
- Does not project a decision (drafts don't) and does not spawn tickets — that's /sorted.
- Does not auto-promote — the block resolves at /sorted.

## Hard rules

- A design block realizes ONE intention; capture it before emitting the draft.
- The draft and its eventual resolved design are ONE artifact (same `design_id` +
  `stamp`) — /sorted overwrites in place, never a second node.
- Re-invoking /design on an open block updates the intention on the same draft.
