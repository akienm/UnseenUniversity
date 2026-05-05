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
TODAY_SLATE=~/.TheIgors/claudecode/$(date +%Y%m%d).slate.txt
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

Always update `~/.TheIgors/claudecode/<closing-day>.slate.txt` (typically
yesterday's file when day-close runs after midnight):
- Final status for each ticket: new, unchanged, done, closed, deferred
- Mark the slate closed (add the `✅ CLOSED` marker at the bottom so the stale-slate check in /context-load recognizes it)

### 4. Day-close audit (MANDATORY)

Always run `/day-close-audit` — all steps. This is not optional. (Renamed
from `/audit` on 2026-04-20 to make role clearer: `/day-close-audit` is
the debris-and-hygiene check.)

Log to: `~/.TheIgors/claudecode/logs/$(date +%Y%m%d).code_maintenance_reviews.log`

### 5. Fix small day-close-audit findings + commit

Always triage each finding:
- Small fix (typo, missing log, dead import): fix now, commit alongside docs.
- Bigger issue: file a /ticket.

When code changed: `/commit`.

### 6. Read the closing slate
```bash
cat ~/.TheIgors/claudecode/<closing-day>.slate.txt
```

### 7. Push tickets to GitHub

Always sync pending tickets to GitHub so Akien has the cloud backup:
```bash
python3 ~/TheIgors/lab/claudecode/github_sync.py push-queue
```

### 8. Sync docs DB
```bash
DB=postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001
IGOR_HOME_DB_URL=$DB python3 ~/TheIgors/lab/claudecode/docs_sync.py sync
```

### 9. Update affected DSBs

For each subsystem touched today: always update the `updated=` date in the
header, then re-run docs_sync after edits so the DB reflects the change.

### 10. Create GitHub Discussion

Always create the day's Discussion — one per day, not a comment on the
master thread:
```bash
gh api graphql -f query='mutation {
  createDiscussion(input: {
    repositoryId: "R_kgDORR89gw",
    categoryId: "DIC_kwDORR89g84C3wqk",
    title: "Day YYYY-MM-DD — <theme>",
    body: "## Done\n- ...\n\n## Tickets\n- ...\n\n## Next\n- ..."
  }) { discussion { number url } }
}'
```

### 11. Post slate to Discussion

Always post the closed slate as a comment on the day's Discussion — that
makes the slate searchable from GitHub.

### 12. Commit docs

Always stage doc directories by name (never `git add -A`):
```bash
git add lab/design_docs/ lab/design_docs_for_igor/ lab/docs/ lab/notes.log
git commit -m "docs: day-close YYYY-MM-DD — <theme>

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
git pull --rebase origin main && git push origin main
```

### 13. /savestate (session-close — include Step 1 summary)

This is the deliberate end-of-session close. Include the session-close
summary (Step 1 of /savestate) — Done and Next lines — so the durable
record has full context when post-compact CC reads it.

### 14. /autocompact

Release debug flag and fire /compact. This is the block-end signal.

## Hard rules
- Every day has a slate — Step 1 always runs, even when day-close fires before context-load on the new day.
- Audit (step 4) always runs — it's the hygiene gate.
- Commits during day-close are always docs-only; source changes belong in /sprint commits.
- Always skip steps with nothing to update (e.g. no DSBs touched today → skip step 9).
