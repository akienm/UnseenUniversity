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

Per theigors/rules/capability-protocol (workflow consumer side): scan the
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

### 2. Claim ticket
```bash
python3 ${CC_WORKFLOW_TOOLS}/cc_queue.py claim <id>
```
Always claim before working — the claim marks the ticket in-progress so the
queue and slate reflect active work. Then add the ticket ID to today's slate
under `## Planned` or `## Ad hoc`.

### 3. Select executor
- **CC inline**: default for code changes in this repo
- **Haiku subagent**: mechanical/checklist work (use the Agent tool, subagent_type=general-purpose with a Haiku model override)
- **Igor**: delegate via `mcp__igor__cc_send` for Igor-domain work (cognition debugging, memory curation, palace edits)

### 4. Review the plan

First, state the plan in one to three sentences: what files will change,
what tests will cover it, what the scope boundary is.

Check inertia before touching anything — the authoritative list lives at
`theigors/rules/safeguards` in the palace. Read it via:
```
memory_get(path="theigors/rules/safeguards")
```
When the plan touches a HIGH-inertia file, always pause and surface it to
Akien for inline pre-approval before coding. Stamp the approval into the
ticket body so it survives compaction.

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

### 6. Pull + work

First, pull to get a clean base:
```bash
git pull --rebase origin main
```
If the working tree is dirty, stash first (`git stash -u`), pull, then pop.

Then write the change. Code first, tests alongside (integration tests hit
real Postgres per `theigors/rules/database` — no mocks), docstrings on
load-bearing files per `theigors/rules/docs-live-in-code`.

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
```bash
cd ~/TheIgors && source venv/bin/activate && python -m pytest tests/ -x -q 2>&1 | grep -A 5 -E "FAIL|ERROR|assert|Exception" | head -120 || true
```
Empty output = all tests pass. Non-empty = failures (grep captures failure line + 5 lines of traceback context). A green run is the signal to stage. A red run means fix the failure first — never commit-and-see.

### 9. Teach Igor — palace deposit (default skip)

Per theigors/rules/capability-protocol: ask "what from this sprint would I
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
python3 ${CC_WORKFLOW_TOOLS}/cc_queue.py done <id> "what was built"
python3 ${CC_WORKFLOW_TOOLS}/cc_queue.py retitle <id> "CLOSED: <bare-title>"
echo "- done: <id> — <summary>" >> ~/.TheIgors/claudecode/$(date +%Y%m%d).slate.txt
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
