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
TODAY_SLATE=${IGOR_HOME:-~/.unseen_university}/claudecode/$(date +%Y%m%d).slate.txt
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

Always update `${IGOR_HOME:-~/.unseen_university}/claudecode/<closing-day>.slate.txt` (typically
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

Log to: `${IGOR_HOME:-~/.unseen_university}/claudecode/logs/$(date +%Y%m%d).code_maintenance_reviews.log`

### 5. Fix small day-close-audit findings + commit

Always triage each finding:
- Small fix (typo, missing log, dead import): fix now, commit alongside docs.
- Bigger issue: file a /ticket.

When code changed: `/commit`.

### 6. Read the closing slate
```bash
cat ${IGOR_HOME:-~/.unseen_university}/claudecode/<closing-day>.slate.txt
```

### 7. Push tickets to GitHub

Always sync pending tickets to GitHub so Akien has the cloud backup:
```bash
python3 ${CC_WORKFLOW_TOOLS}/github_sync.py push-queue
```

### 8. Sync docs DB
```bash
DB=postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001
UU_HOME_DB_URL=$DB python3 ${CC_WORKFLOW_TOOLS}/docs_sync.py sync
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

### 12. Write palace.days.* node

Always write a roll-up node for the closing day. Pull summary from the
slate (Done today section) and any open sessions list:

```bash
CLOSING_DATE=<YYYY-MM-DD>   # the day being closed
DATESTAMP=<YYYYMMDD>        # same, no dashes
SLATE=${IGOR_HOME:-~/.unseen_university}/claudecode/${DATESTAMP}.slate.txt

# Build content from Done today section of the closing slate
DONE_SECTION=$(sed -n '/^## Done today/,/^## /p' "$SLATE" | grep "^- " | head -20)

python3 - <<'EOF'
import os, json, psycopg2, psycopg2.extras
from datetime import datetime, timezone

pg = os.environ.get("UU_HOME_DB_URL", "postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001")
closing_date = os.environ.get("CLOSING_DATE", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
datestamp = closing_date.replace("-", "")
done_section = os.environ.get("DONE_SECTION", "(see slate)")

# Gather palace.sessions.* nodes for this day (if any)
conn = psycopg2.connect(pg)
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute(
        "SELECT path, title FROM adc.palace WHERE path LIKE %s ORDER BY path",
        (f"palace.sessions.{datestamp}%",),
    )
    sessions = [dict(r) for r in cur.fetchall()]

session_lines = "\n".join(f"- {s['title']} ({s['path']})" for s in sessions) or "(none recorded)"
content = f"## Done\n{done_section}\n\n## Sessions\n{session_lines}\n"
title = f"Day {closing_date}"
metadata = psycopg2.extras.Json({
    "tags": ["day", "rollup"],
    "date": closing_date,
    "session_count": len(sessions),
})

with conn.cursor() as cur:
    cur.execute(
        """INSERT INTO adc.palace (path, title, content, node_type, updated_at, metadata)
           VALUES (%s, %s, %s, 'rollup', now(), %s)
           ON CONFLICT (path) DO UPDATE
               SET title=EXCLUDED.title, content=EXCLUDED.content,
                   updated_at=EXCLUDED.updated_at, metadata=EXCLUDED.metadata""",
        (f"palace.days.{datestamp}", title, content, metadata),
    )
conn.commit()
conn.close()
print(f"palace.days.{datestamp} written ({len(sessions)} sessions).")
EOF
```

Also write a flat-file echo (file is secondary — palace is canonical):
```bash
mkdir -p ${IGOR_HOME:-~/.unseen_university}/claudecode/palace_echo
python3 -c "
import os, psycopg2, psycopg2.extras
pg = os.environ.get('UU_HOME_DB_URL','postgresql://igor:choose_a_password@127.0.0.1/Igor-wild-0001')
datestamp = '${DATESTAMP}'
conn = psycopg2.connect(pg)
with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
    cur.execute('SELECT content FROM adc.palace WHERE path = %s', (f'palace.days.{datestamp}',))
    rows = cur.fetchall()
if rows:
    open(os.path.expanduser(f'${IGOR_HOME:-~/.unseen_university}/claudecode/palace_echo/day_{datestamp}.md'),'w').write(rows[0]['content'])
    print('echo written')
conn.close()
"
```

### 13. Commit docs

Always stage doc directories by name (never `git add -A`):
```bash
git add lab/design_docs/ lab/design_docs_for_igor/ docs/ lab/notes.log
git commit -m "docs: day-close YYYY-MM-DD — <theme>

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>"
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

### 15. /autocompact

Release debug flag and fire /compact. This is the block-end signal.

## Hard rules
- Every day has a slate — Step 1 always runs, even when day-close fires before context-load on the new day.
- Audit (step 4) always runs — it's the hygiene gate.
- Commits during day-close are always docs-only; source changes belong in /sprint commits.
- Always skip steps with nothing to update (e.g. no DSBs touched today → skip step 9).
- Step 12 (palace.days.*) always runs — even if Done today is empty, the node records the day.
