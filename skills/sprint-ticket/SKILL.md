---
name: sprint-ticket
description: Single-ticket execution unit — capability check, claim, build, test, commit, close, savestate. Called by /sprint and /sprint-batch. Args: ticket ID.
model: sonnet
---

# /sprint-ticket — Single-ticket sprint

The atomic sprint unit. Takes a ticket ID, runs it from claim to close,
writes savestate on completion. Does NOT fire /autocompact — that's the
caller's job at block-end.

## Args
- `/sprint-ticket T-xxx` — sprint a specific ticket

## Steps

### 1. Capability check

Per unseenuniversity/rules/capability-protocol (workflow consumer side): scan the
ticket's tags and Affected files against the capability surface. If a
minion or device on the rack would do the work better than CC inline,
surface the delegate option as a one-line command **before** the claim.
The prompt is mandatory; the delegate action is not — Akien decides.

Capability surface to scan:
- Available MCP tools (deferred tool list — `mcp__igor__*`, `mcp__datacenter__*`) — names tell you what minions/devices are reachable.
- `mcp__datacenter__datacenter_manifest` — full per-device capability map if you need detail beyond tool names.

Matching heuristics (when any match, surface the option):
- Ticket tag includes `Database` → `mcp__igor__db_query`
- Ticket tag includes `Cognition` / `Debug` → Igor cognition-debug capability
- Ticket tag includes `Reading` / `Memory` → Igor memory tools
- Affected files under `wild_igor/igor/` AND ticket scope is "implement inside Igor" → consider Igor self-coding via cc_send

Output shape (one line, before Step 2 — Claim):
```
CAPABILITY CHECK: <tag/match> matches <capability> — delegate via:
  <one-line command>
Proceed inline anyway? (y to claim CC-side, or fire the delegate)
```

When no match: silent — proceed to Step 2 directly.

### 2. Confirm ticket is in_progress

Tickets arrive pre-claimed — cmd_next atomically marks the ticket in_progress
before handing it to the sprint runner. Verify it is in the expected state:
```bash
python3 ${CC_WORKFLOW_TOOLS}/cc_queue.py show <id> | grep '"status"'
```
If status is not `in_progress`, something went wrong — do not proceed; surface
to Akien. Then add the ticket ID to today's slate under `## Planned` or `## Ad hoc`.

### 3. Select executor
- **CC inline**: default for code changes in this repo
- **Haiku subagent**: mechanical/checklist work (use the Agent tool, subagent_type=general-purpose with a Haiku model override)
- **Igor**: delegate via `mcp__igor__cc_send` for Igor-domain work (cognition debugging, memory curation, palace edits)

### 4. Review the plan

First, state the plan in one to three sentences: what files will change,
what tests will cover it, what the scope boundary is.

Check inertia before touching anything — the authoritative list lives at
`unseenuniversity/rules/safeguards` in the palace. Read it via:
```
memory_get(path="unseenuniversity/rules/safeguards")
```
When the plan touches a HIGH-inertia file, always pause and surface it to
Akien for inline pre-approval before coding. Stamp the approval into the
ticket body so it survives compaction.

For L/XL or HIGH-inertia tickets: before finalizing the plan, ultrathink —
reason deeply about what could go wrong, what constraints are hidden, whether
the scope is truly minimal, and whether the affected files list is complete.
Do not proceed to Step 5 (infrastructure brief) until the ultrathink pass is
complete and the plan survives scrutiny.

### 5. Infrastructure brief (D-scaffold-not-correct-2026-04-21)

After the inertia check, surface a one-screen infrastructure brief for the
touched areas (MCP tools, proxies, base classes, IMAP buses, channels).

```bash
python3 ${CC_WORKFLOW_TOOLS}/sprint_infrastructure_brief.py \
  <file1> <file2> ...
```

Read the output and ask: "does my plan use the preferred forms listed here?"
If the plan proposes a deprecated form (raw psql, channel.py direct write,
print()), amend before coding. Also run `/audit-precode` on the plan text
before Step 6.

**Optional: Librarian research (graceful degradation)**
When `mcp__librarian__*` tools are available (check deferred tool list),
call before coding to surface related prior work:
```
mcp__librarian__research(topic="<ticket title or key term>", depth="brief")
```
Surface as one line: `Librarian: <findings>`. When unavailable or errors, skip silently — never block the sprint on librarian.

### 6. Pull + work

First, pull to get a clean base:
```bash
git pull --rebase origin main
```
If the working tree is dirty, stash first (`git stash -u`), pull, then pop.

Then write the change. Code first, tests alongside (integration tests hit
real Postgres per `unseenuniversity/rules/database` — no mocks), docstrings on
load-bearing files per `unseenuniversity/rules/docs-live-in-code`.

### 7. Cleanup (REQUIRED)

Always review the diff before staging:
```bash
git diff --stat && git diff
```
Every file in the diff exists on purpose. Remove: debug prints,
commented-out code, unused imports, replaced functions, single-use helpers
(inline them), temp files. A clean diff is the signal that the sprint is
ready to ship.

### 8. Test

Always run tests before commit:
```
python run test
```
Empty output = all tests pass. Non-empty = failures (grep captures failure line + 5 lines of traceback context). A green run is the signal to stage. A red run means fix the failure first — never commit-and-see.

### 8.5. Post-sprint grader (advisory)

After tests pass, spawn a fresh subagent to grade the diff against the ticket's Test plan.
The grader is advisory — it never blocks the close.

```bash
# Extract staged diff and ticket Test plan for grader
DIFF=$(git diff --staged)
TICKET_ID="<id>"
TEST_PLAN=$(python3 ${CC_WORKFLOW_TOOLS}/cc_queue.py show $TICKET_ID | python3 -c "import sys,json,re; d=json.load(sys.stdin); m=re.search(r'\*\*Test plan:\*\*(.+?)(\*\*|$)',d.get('description',''),re.S); print(m.group(1).strip() if m else 'no test plan')")
```

Pass DIFF + TEST_PLAN to a Haiku subagent:
```
Subagent prompt: "Grade this sprint. Test plan: {TEST_PLAN}\n\nDiff:\n{DIFF}\n\nAre the tests described in the Test plan present in the diff? List any gaps. One paragraph, advisory only."
```

If Test plan says "no tests because: <reason>" — skip silently.
Surface gaps inline as a single note before step 10. Do not block commit.

### 9. Teach Igor — palace deposit (default skip)

Per unseenuniversity/rules/capability-protocol: ask "what from this sprint would I
deposit into Igor's palace?"

**Default answer: skip.** Most sprint work is mechanical. Deposit only when
non-obvious reasoning emerged: a design choice the ticket didn't anticipate,
a hidden invariant the refactor surfaced, a workaround whose mechanism the
next reader needs, a bug fix whose ROOT differed from the symptom.

When non-skip: propose 0–N palace nodes (path + title + content), surface
to Akien inline for review, then INSERT via psql after approval.

### 10. Commit + push

Always stage files specifically by name (not `git add -A` / `git add .`).
```bash
git add <specific files>
git commit -m "$(cat <<'EOF'
feat/fix/docs: description

Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
EOF
)"
git pull --rebase origin main && git push origin main
```
Always let pre-commit hooks run. Push non-force to main.

### 11. Close ticket

Always close with a one-line summary of what actually shipped:
```bash
python3 ${CC_WORKFLOW_TOOLS}/cc_queue.py close <id> "what was built"
```
```
python run done-slate <id> "what was built"
```

### 12. Retroactive incidental ticket

When the commit includes changes unrelated to the claimed ticket, always
draft a new ticket and immediately close it for the incidental fix — every
change has a ticket.

### 13. /savestate

Always run /savestate at ticket close — records what was built, marks
the state change durable. This is a mid-session flush: skip the
session-close summary (Step 1 of /savestate).

## Hard rules
- Always sprint from a ticket — this skill requires a valid ticket ID.
- Cleanup (step 7) is the last pre-commit act — the debris review is load-bearing.
- Always let pre-commit hooks run; always push non-force to main.
- Always stage files by name.
- When tests pass and no secrets are in the diff, commit proceeds without asking.
