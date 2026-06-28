---
name: sprint-ticket
description: Single-ticket execution unit — capability check, claim, build, test, commit, close, savestate. Called by /sprint and /sprint-batch. Args: ticket ID.
---

# /sprint-ticket — Single-ticket sprint

The atomic sprint unit. Takes a ticket ID, runs it from claim to close,
writes savestate on completion. Does NOT fire the native compact — that's the
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
- Available MCP tools (deferred tool list — `mcp__librarian__*`, `mcp__datacenter__*`) — names tell you what minions/devices are reachable.
- `mcp__datacenter__datacenter_manifest` — full per-device capability map if you need detail beyond tool names.

Matching heuristics (when any match, surface the option):
- Ticket tag includes `Database` → `mcp__librarian__db_query`
- Ticket tag includes `Cognition` / `Debug` → Igor cognition-debug capability
- Ticket tag includes `Reading` / `Memory` → Igor memory tools
- Affected files under `unseen_university/devices/igor/` AND ticket scope is "implement inside Igor" → consider Igor self-coding via cc_send

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
python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/cc_queue.py show <id> | grep '"status"'
```
If status is not `in_progress`, something went wrong — do not proceed; surface
to Akien. Then add the ticket ID to today's slate under `## Planned` or `## Ad hoc`.

### 2.3. Anticipatory pre-brief

Run both brief scripts immediately after confirming in_progress — before reading any files:
```bash
python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/sprint_preflight_brief.py <id>
python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/pre_inference_assemble.py <id>
```

`sprint_preflight_brief.py` surfaces: reset history, open/closed sibling tickets, file-proximity matches.

`pre_inference_assemble.py` surfaces: matched design patterns (by keyword overlap against `docs/design_patterns_inventory.md`), file symbol maps for affected files, domain terms. Read the symbol map instead of opening each file manually — it replaces exploratory file-discovery tool calls.

Combined token budget: ~800 tokens max. Read both before forming the plan.

### 2.4. BuilderReport freshness check (fast path, no LLM)

When the ticket description contains `**Builder report:**`, run freshness_check()
to surface in_flight conflicts and staleness. Non-fatal — skip if no report present.

```bash
STORED_REPORT=$(python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/cc_queue.py show <id> 2>/dev/null \
  | python3 -c "
import sys,json,re
d=json.load(sys.stdin)
m=re.search(r'\*\*Builder report:\*\*\s*(\{.*?\})', d.get('description',''), re.S)
print(m.group(1) if m else '{}')
")
if [ "$STORED_REPORT" != "{}" ]; then
  python3 -m unseen_university.devices.classifier.cli freshness --report-json "$STORED_REPORT" 2>/dev/null || true
fi
```

When freshness output shows `"stale": true` or non-empty `"warnings"`, surface to Akien
before proceeding. When `"stale": false` and no warnings — proceed without comment.

### 3. Select executor
- **CC inline**: default for code changes in this repo
- **Haiku subagent**: mechanical/checklist work (use the Agent tool, subagent_type=general-purpose with a Haiku model override)
- **Igor**: delegate via `mcp__librarian__cc_send` for Igor-domain work (cognition debugging, memory curation, palace edits)

### 4. Review the plan

First, state the plan in one to three sentences: what files will change,
what tests will cover it, what the scope boundary is.

Check inertia before touching anything — the authoritative list lives in the
flat-file rules store. Read it via:
```
cat "${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/runtime/memory/rules/"*safeguards* 2>/dev/null
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
python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/sprint_infrastructure_brief.py \
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
TEST_PLAN=$(python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/cc_queue.py show $TICKET_ID | python3 -c "import sys,json,re; d=json.load(sys.stdin); m=re.search(r'\*\*Test plan:\*\*(.+?)(\*\*|$)',d.get('description',''),re.S); print(m.group(1).strip() if m else 'no test plan')")
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

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
git pull --rebase origin main && git push origin main
```
Always let pre-commit hooks run. Push non-force to main.

### 10.5. Refresh clan.code_index for committed files

After push, re-index only the files touched in the sprint so the orientation
classifier sees the new symbols immediately on the next ticket.

```bash
FILES=$(git diff --name-only HEAD~1 HEAD 2>/dev/null)
if [ -n "$FILES" ]; then
  python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/code_indexer.py --files $FILES 2>&1 | tail -1 || true
fi
```

Non-fatal — log and continue if the indexer fails or the DB is down.
Skip silently when `HEAD~1` doesn't exist (first commit in repo).

### 11. Close ticket (proof-on-close gate — D-proof-on-close-2026-06-20)

A close now passes the **proof-on-close gate**: `cc_queue.py close` is REFUSED
unless the ticket points at a HEAD-valid proof, or the close NAMES THE MISSING
PROOF-LEVER. "Done" is no longer a free claim (CP1). Two honest paths:

**Proven** — emit a commit-bound proof first (red→green a hollow build couldn't
produce), commit it, then close:
```bash
python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/proof_emitter.py \
  --thing "<what>" --intention "<one falsifiable claim>" \
  --test "tests/test_x.py::test_y" --ticket <id>
git add -A && git commit -m "proof: <id>"   # commit the emitted proof JSON
python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/cc_queue.py close <id> "what was built"
```

**shipped-unproven** — when a proof can't yet be defined (conceptual ticket, or
the proof step isn't wired for this class), close honestly and name the lever
we still lack. Visible backlog, never a silent "done":
```bash
python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/cc_queue.py close <id> "what was built" \
  --shipped-unproven "<the proof-lever we still lack — e.g. no harness for <X>>"
```

Until proof-emission is wired into this skill's flow (separate ticket), default
to `--shipped-unproven` with a concrete missing-lever reason rather than a vague
one — the reason IS the backlog entry.
```
python run done-slate <id> "what was built"
```

Then check whether closing this ticket completes a decision's spawned_tickets list:
```bash
python3 "${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/outcome_check.py" <id>
```
When all spawned_tickets in a decision are now closed, this prints:
`🏁 Decision D-xxx is fully shipped — run /outcome D-xxx: <hypothesis>`
Surface this to Akien and offer to run /outcome. Silent when the decision is incomplete.

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
