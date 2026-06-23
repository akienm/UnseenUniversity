# UnseenUniversity Workflow Map

Single source of truth for how the skills fit together. Rendered by the
`/workflow` skill and by `uu workflow`. When the workflow changes, edit THIS
file — never duplicate the map elsewhere.

```
THE TRACKING STACK
══════════════════════════════════════════════════════════════

  Goals (G-xxx)
    /goal new|list|update|block|retire
    /audit-goal         ← 7 checks + "better way?" challenge
    Stored: palace.goals.*

  Open Questions (Q-xxx)           ← things not ready to decide yet
    /question "text" | /questions | /question promote Q-xxx
    Stored: palace.questions.*

  Hypothesis                       ← extracted at /sorted time (3 questions)
    /audit-hypothesis   ← 5 checks + "better hypothesis?" challenge
    Stored on: decision record

  Design conversation
    /design             ← optional framing opener, writes DESIGN_START marker

  Decision (D-xxx)                 ← design call + hypothesis + goal link
    /sorted             ← summarize → extract hypothesis → audit-hypothesis
                           → audit-design → draft tickets → audit-ticket
                           → file tickets → write palace node → log
    /migrate-decisions  ← project decision markdown into the fs memory store
    /audit-design       ← 9 checks + "better architecture?" challenge
    /audit-ticket       ← 16 checks + "simpler implementation?" challenge

  Tickets (T-xxx)
    /ticket             ← create or update a single ticket
    /query-ticket       ← read-only: what should I work on next? (canonical entry)
    /sprint-ticket T-xxx← claim → build → test → commit → close → savestate
    /sprint-batch       ← multi-ticket: topo-sort → shared setup → per-ticket loop
    /sprint-loop        ← continuous sprint mode
    /fixit              ← /sorted + /sprint-batch in one shot

  Proof-on-close                   ← a close points at a proof, or names the missing lever
    proof_emitter.py    ← emit a commit-bound red→green proof before closing

  Outcomes                         ← closes the learning loop
    /outcome D-xxx      ← review hypothesis vs evidence → confirmed/falsified
    Stored on: decision record + palace node

  Goal KRs updated                 ← /outcome writes KR progress to G-xxx

══════════════════════════════════════════════════════════════
WEEKLY TRACKS (Fridays in day-close, or standalone)

  /eval-run            ← 5 capability questions → feeds goal KRs
  /weekly-retro        ← hypothesis confirmation rate + goal progress
  /audit-expert        ← 3 random experts (weekly) or full 11 (monthly, 1st Monday)

DAILY
  /context-load        ← session startup: slate + rules + palace + channel + inbox
  /day-close           ← end of day: savestate → close slate → audit → docs → push
  /day-close-audit     ← 20-step code health check (runs inside day-close)
  /savestate           ← flush in-flight state to slate (mid-session or session-close)
  /autocompact         ← release debug flag + fire /compact (end of work block)
  /recover             ← re-orient after a /rewind (read slate + git, trust them)

AUDIT FAMILY
  /audit-goal          ← goal quality gate (7 checks)
  /audit-hypothesis    ← hypothesis quality gate (5 checks)
  /audit-design        ← decision quality gate (9 checks)
  /audit-ticket        ← ticket quality gate (16 checks)
  /audit-precode       ← plan review before coding
  /audit-smell         ← code smell scan
  /audit-debris        ← file placement + dead code
  /audit-day           ← process lens audit
  /audit-audits        ← meta: are the audits working?
  /audit-expert        ← discipline lens: 11 experts

OTHER SKILLS
  /design              ← open a design block (optional prefix to /sorted)
  /dream               ← manually trigger Igor's dreaming pass
  /note                ← quick note to slate
  /readinbox           ← check CC inbox for Igor notifications
  /test-fix            ← diagnose and fix a failing test
  /commit              ← structured git commit
  /export-chat         ← export conversation to file
  /skills-sync         ← sync skills from UnseenUniversity/skills/ to ~/.claude/skills/
  /workflow            ← render this map (or run `uu workflow`)

══════════════════════════════════════════════════════════════
WHERE AM I? — QUICK GUIDE

  "I have a new idea"
    → /question "idea" to park it, OR
    → /design → /sorted to turn it into tickets immediately

  "I want to start work"
    → /context-load (if session start)
    → /query-ticket (what's next?) then /sprint-batch today-slate

  "I just had a design conversation"
    → /sorted (will extract hypothesis, audit, file tickets)

  "Tickets are filed, ready to build"
    → /sprint-batch decision:D-xxx

  "Work is done, how did it go?"
    → /outcome D-xxx (review the hypothesis)

  "End of day"
    → /day-close (covers savestate, audit, docs, push)
    → If Friday: /eval-run + /weekly-retro + /audit-expert auto-run

  "Something is broken / stuck"
    → /readinbox (check for Igor notifications)
    → /test-fix (if tests are red)

  "I want the big picture health check"
    → /audit-expert (weekly: 3 experts; monthly: all 11)

  "I don't know what's in the queue"
    → /query-ticket, or python3 ${CC_WORKFLOW_TOOLS}/cc_queue.py list

  "I want to check goal progress"
    → /goal list
    → /eval-run (current capability snapshot)

══════════════════════════════════════════════════════════════
THE QUESTION AT EVERY STEP: Is there a better way?
  goal → audit-goal challenge
  hypothesis → audit-hypothesis challenge
  design → audit-design challenge
  ticket → audit-ticket challenge
══════════════════════════════════════════════════════════════
```
