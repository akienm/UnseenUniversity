---
name: day-close
description: End-of-day ritual — savestate, close slate, audit, update docs, commit.
model: haiku
model_exception: /day-close-audit step escalates to Sonnet for simplification review
---

# /day-close — Close out the day

## Steps

### 1. Ensure today's slate exists

day-close typically runs at the start of the next day (after midnight
rollover). Every day has a slate. When the date has ticked over and the
current-day slate doesn't exist yet, always create it now before closing
the day being ended — that keeps the "every day has a slate" invariant
intact.
```bash
TODAY_SLATE=${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/runtime/memory/slates/$(date +%Y%m%d).slate.txt
if [ ! -f "$TODAY_SLATE" ]; then
  cat > "$TODAY_SLATE" <<EOF
# Slate $(date +%Y-%m-%d)

## Notes

## In-flight
NONE

## Planned

## Ad hoc

## Done today
EOF
fi
```

### 2. /savestate

Always flush all in-flight state first — a clean baseline makes the rest
of day-close idempotent.

### 3. Close the slate for the day being ended

Always update `${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/runtime/memory/slates/<closing-day>.slate.txt` (typically
yesterday's file when day-close runs after midnight):
- Final status for each ticket: new, unchanged, done, closed, deferred
- Reorder sections so the closed slate is optimized for future CC reading
- Mark the slate closed (add the `✅ CLOSED` marker at the bottom so the stale-slate check in /context-load recognizes it)

**Section order at close-time** (Done first — answers "what shipped?" immediately):
1. `## Done today` — everything that closed, with commit hashes
2. `## Notes` — short-term reminders still relevant
3. Session-close summary blocks (`## Session close: ...` with Done/Next lines)
4. `## In-flight` — what was mid-work at close
5. `## Ad hoc` — reactive additions during the day
6. `## Planned` — what was planned but not finished
7. `✅ CLOSED` — terminator marker

Sections with no content (empty `## Planned`, empty `## Ad hoc`) can be omitted at close time — they add noise without signal. The `## Done today` and `✅ CLOSED` sections are always present.

### 4. Day-close audit (MANDATORY)

Always run `/day-close-audit` — all steps. This is not optional. (Renamed
from `/audit` on 2026-04-20 to make role clearer: `/day-close-audit` is
the debris-and-hygiene check.)

Log to: `$HOME/.unseen_university/logs/day-close/$(date +%Y%m%d).code_maintenance_reviews.log`

### 4.5. Gate sweep (T-day-close-gate-sweep)

Clear elapsed date-only gates — removes the `[gate: <past-date>]` cosmetic
noise from READY tickets. Safe and idempotent. Skips id-token gates (those
are handled live by gate_clear on ticket close).

```bash
python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/cc_queue.py sweep-gates
```

Log the count from the output. No action needed when count is 0.

### 4.6. Stash hygiene — analyze + clear stale git stashes

A lingering `git stash` is hidden divergent state. It survives across sessions
invisibly, and the next `git stash pop`/`stash -u` dance resurrects it onto the
wrong base — conflict markers in tracked files, orphaned untracked files, a
working tree that looks corrupt. (This bit us 2026-06-25: a 841-commit-old stash
popped during a baseline check and looked like a SyntaxError in shim.py.) The
rule: **the stash list ends the day empty.**

```bash
git -C "${UU_ROOT:-$HOME/dev/src/UnseenUniversity}" stash list
```

For each entry: preserve, analyze, then drop.
1. **Preserve** — `git stash show -p "stash@{N}" > <scratchpad>/stash_N.patch`
   (never drop without a saved patch first).
2. **Analyze** — note the base commit and how stale it is
   (`git rev-list --count <base>..HEAD`); check whether the content already
   landed in main (`git apply --check` fails ⇒ diverged/superseded).
3. **Drop** — once preserved + analyzed, `git stash drop "stash@{N}"`
   (or `git stash clear` when all are confirmed stale). Report what was cleared.

Never run `stash pop`/`stash -u` to test at an old commit — use `git worktree`
or `git show <ref>:<path>` instead, so a stale stash is never resurrected onto
the live tree. (See memory [[git-stash-is-hidden-divergent-state]].)

### 5. Fix small day-close-audit findings + commit

Always triage each finding:
- Small fix (typo, missing log, dead import): fix now, commit alongside docs.
- Bigger issue: file a /ticket.

When code changed: `/commit`.

### 6. Read the closing slate
```bash
cat ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/runtime/memory/slates/<closing-day>.slate.txt
```

<!-- (removed: GitHub ticket-backup + docs-DB-sync steps — those helper scripts were never built; dropped functionality tracked in T-skills-deadstep-followup) -->

<!-- (removed: Step 9 "Update affected DSBs / docs_sync" — DSBs and docs_sync are retired; the architecture/ intention-points in devlab/runtime/memory/architecture/ are the subsystem record now.) -->

### 10. Create GitHub Discussion

Always create the day's Discussion — one per day, not a comment on the
master thread:
```bash
gh api graphql -f query='mutation {
  createDiscussion(input: {
    repositoryId: "R_kgDOSOSpkA",
    categoryId: "DIC_kwDOSOSpkM4C8i8p",
    title: "Day YYYY-MM-DD — <theme>",
    body: "## Done\n- ...\n\n## Tickets\n- ...\n\n## Next\n- ..."
  }) { discussion { number url } }
}'
```

### 11. Post slate to Discussion

Always post the closed slate as a comment on the day's Discussion — that
makes the slate searchable from GitHub.

### 12. Write the day roll-up to the memory store

Always emit a flat-file roll-up note for the closing day, pulled from the
slate's `## Done today` section. The closed slate is the source of truth; this
is the greppable one-line-per-day index in `devlab/runtime/memory/notes/`:

```bash
CLOSING_DATE=<YYYY-MM-DD>   # the day being closed
DATESTAMP=<YYYYMMDD>        # same, no dashes
SLATE=${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/runtime/memory/slates/${DATESTAMP}.slate.txt
DONE_SECTION=$(sed -n '/^## Done today/,/^## /p' "$SLATE" | grep "^- " | head -20)

DONE_SECTION="$DONE_SECTION" CLOSING_DATE="$CLOSING_DATE" python3 - <<'EOF'
import os, json, subprocess, sys
from pathlib import Path
from unseen_university._uu_root import uu_root
closing = os.environ.get("CLOSING_DATE", "")
done = os.environ.get("DONE_SECTION", "(see slate)")
body = {"title": f"Day {closing}", "text": f"## Done\n{done}\n"}
open("/tmp/day_rollup.json", "w").write(json.dumps(body))
TOOLS = str(Path(uu_root()) / "devlab" / "claudecode")  # uu_root() returns str, not Path
subprocess.run([sys.executable, f"{TOOLS}/memory_emit.py", "--category", "notes",
    "--emitter", "cc.0", "--kind", "note", "--namespace",
    f"day-{closing.replace('-', '')}", "--body-file", "/tmp/day_rollup.json"], check=True)
print(f"day-{closing.replace('-', '')} roll-up written.")
EOF
```

### 13. Commit docs

Always stage doc directories by name (never `git add -A`):
```bash
git add devlab/runtime/memory/ docs/
git commit -m "docs: day-close YYYY-MM-DD — <theme>

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
git pull --rebase origin main && git push origin main
```

### 13.5. Friday: /eval-run + /weekly-retro + /audit-expert

**Only on Fridays** (`date +%u` = 5). Skip on other days.

```bash
if [ "$(date +%u)" = "5" ]; then
  echo "Friday detected — running weekly capability + expert audit pass"
  # Step A: eval-run
  # /eval-run  (run inline per that skill's steps)

  # Step B: weekly-retro
  # /weekly-retro  (run inline per that skill's steps)

  # Step C: audit-expert (weekly mode — 3 random experts)
  # /audit-expert  (weekly default; runs after retro so retro findings inform expert context)
fi
```

Also on the **first Monday of each month** (`date +%u` = 1 and `date +%d` ≤ 7):
run `/audit-expert --mode=monthly` (full 11-expert panel).

```bash
if [ "$(date +%u)" = "1" ] && [ "$(date +%d)" -le 7 ]; then
  echo "First Monday — running full monthly expert panel"
  # /audit-expert --mode=monthly
fi
```

### 14. /savestate (session-close — include Step 1 summary)

This is the deliberate end-of-session close. Include the session-close
summary (Step 1 of /savestate) — Done and Next lines — so the durable
record has full context when post-compact CC reads it.

## Hard rules
- Every day has a slate — Step 1 always runs, even when day-close fires before context-load on the new day.
- Audit (step 4) always runs — it's the hygiene gate.
- Commits during day-close are always docs-only; source changes belong in /sprint commits.
- Step 12 (day roll-up) always runs — even if Done today is empty, the note records the day.
