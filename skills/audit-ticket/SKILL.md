---
name: audit-ticket
description: Filing-time ticket audit — quality gate for every ticket before it lands in the queue. Runs duplicate detection, already-done check, scope/size/HIGH-inertia checks, palace design-rules, build-tightness grade, plus validation steps, remediation plan, rollback (HIGH-inertia), logging requirements, observability assertion, and split test. Called by /decided on each drafted ticket. Returns PASS / AMEND / SPLIT / DISCARD. Model: Haiku.
model: haiku
---

# audit-ticket — Filing-time ticket quality gate

Quality gate for every ticket before it lands in queue.json. Runs the full
filing-time checklist in order.

## Input

A drafted ticket dict (id, title, size, tags, description, decision_id).

## Checks (in order)

### 1–8. Filing-time checks

Run these first on every ticket.

### 9. Validation steps (how do we observe success in runtime?)

The description must answer: **how will we know it worked in production, not
just in tests?** Required specifics:
- A log line that signals success (e.g. "STEP3 posted ready for ticket=T-xxx")
- A DB row that appears or changes
- A channel message that fires
- A metric or count that moves

"Tests pass" does not count as a runtime validation step. "The habit fires"
needs a log line to confirm it.

Missing → AMEND: add `Validation: <what runtime observation confirms success>`

### 10. Validation remediation (cleanup after tests)

When the ticket involves DB rows, test fixtures, log files, or network state
that tests create: the description must say how to clean up.

- DB rows: "test fixture teardown via conftest.pg_test_schema"
- Log noise: "test_mode flag suppresses log entries"
- Channel messages: "channel mocked in test"

Silence = AMEND: add `Cleanup: <how test artifacts are removed>`

### 11. Rollback plan (HIGH-inertia only)

When the ticket touches a HIGH-inertia file (brainstem/, memory/models.py,
reasoners/base.py):

- Description must include: "Rollback: `git revert <hash>` restores previous
  behavior because X" (or explain why rollback isn't needed)
- Silence = AMEND

### 12. Logging requirements

Check the description for: does the new code path have a log line that would
immediately point at it when it breaks in production?

Pattern: any `try/except` block, any silent-return-False path, any fanout
(habits, TWM push) — these MUST have a log statement.

If the ticket proposes any of these patterns without a logging requirement,
add: `Logging: try/except at <file>:<lineno> must log ERROR with surrounding
state on exception`

### 13. Observability assertion

Every non-trivial ticket must be able to answer: "If this breaks in prod,
which log line points at it within 5 minutes?"

Required: one explicit log line (level + message) in the description or test
plan that serves as the observability hook.

Missing = AMEND: add `Observable via: log.<level>("<message>") at <location>`

### 14. Split test (size + verb count)

Count distinct action verbs in the check_body:
- add, remove, create, delete, modify, rename, move, update, fix, extend, build

When size > S AND verb count >= 3 in the same semantic unit → propose split.

Output: `SPLIT: propose T-a (verbs X, Y) + T-b (verb Z)`

### 15. Audit-emphasis tag

Does the ticket description include an `audit-emphasis` directive?
- `needs-deep-smell`: flag for extra audit-smell attention
- `doc-only`: skip audit-smell for this ticket (pure doc change)
- Absent: normal audit routing

Note the tag (or absence) in output so downstream audit routing can act on it.

### 16. Two-sided build for capability tickets

Enforces unseenuniversity/rules/capability-protocol/two-sided-build: a ticket that
adds a new capability must ship handler AND skill consumer together — never
just one half.

**Trigger:** the ticket is a capability ticket. Detect via either:
- `tags` includes `Capability`, OR
- description mentions `MCP capability`, `shim`, or `handler` in a creator
  sense (not just referencing existing ones)

**Skip when:** the description carries an explicit exemption line of the
form `exempt from unseenuniversity/rules/capability-protocol/two-sided-build` with
a stated reason (e.g. "consumes existing capability surface, doesn't create
one" — pure skill-consumer tickets, OR "is the enforcer, not a consumer" —
the rule-implementing ticket itself). Note the exemption in output.

**Check:** scan Affected files for both sides:
- HANDLER paths (any one match): `UnseenUniversity/devices/`,
  `lab/claudecode/mcp_*`, `wild_igor/igor/**/device*.py`,
  `UnseenUniversity/**/capability*.py`
- SKILL CONSUMER paths (any one match): `/home/akien/.claude/skills/*`

Both sides present → PASS this check.

Only one side present (and no exemption) → **SPLIT** with sequencing:
```
SPLIT: capability ticket missing one side
  - T-<orig>-handler — build the capability (handler files only)
  - T-<orig>-consumer — migrate skills to use it (skill files only)
    GATE: T-<orig>-handler closed
```
The handler ticket always runs first; the consumer is gated on the
handler closing. Never silently auto-split — emit the proposal for human
review.

**Test cases (documented for CC.1 integration testing — T-cc1-test-minion):**
| Ticket shape | Expected verdict on check 16 |
|---|---|
| tags=[Capability], handler file + skill file | PASS |
| tags=[Capability], handler file only | SPLIT |
| tags=[Capability], skill file only | SPLIT |
| tags=[Capability], skill file only + exempt-line in body | PASS (note exemption) |
| tags=[Skills], no handler/MCP keywords in body | SKIP (not a capability ticket) |

### 17. Skill feedback-loop check (when Affected files touch skills/)

Scan the `Affected files:` section for any `skills/<name>/` path. For each matched
skill name, run `/audit-feedback` on it:

```bash
python skills/audit-feedback/run check <name>
```

If the skill dir is absent or the run script doesn't exist: skip silently, note
`[audit-feedback] skills/<name>/ not yet deployed — skipped`.

If `/audit-feedback` returns AMEND: add each missing property to the findings as an
advisory item. These do **not** auto-block the verdict — they require Akien to
acknowledge the gap explicitly (he may accept the risk or defer fixing the skill).

Example finding:
```
- [feedback-loop] skills/note/ missing: self-verification, failure-surface
  → add read-back assertion after write; add sys.exit(1) on error path
```

**Match pattern:** `skills/([a-z0-9_-]+)/` anywhere in the description text.

### 18. Hold-status dependency gate (D-hold-gate-enforcement-2026-06-06)

When the drafted ticket has `status: hold`, the description must name what it
is blocked on. Accept either:
- A ticket reference — `T-<slug>` anywhere in the description, OR
- An explicit Akien action — text matching `Akien:` (case-insensitive) in the
  description

**Both absent → AMEND:**
```
[hold-gate] status=hold but no named dependency found.
  Add one of:
  - "Blocked on: T-<slug>" with the ticket ID this depends on, OR
  - "Akien: <specific action required>" with the action Akien must take
```

**Why this fires:** Vague holds (imagined external impact, hypothetical future
components, over-cautious concerns unrelated to this project) accumulate and
confuse the queue. A hold is only valid when there is a specific named thing
that must happen first.

**Skip when:** `status` is anything other than `hold`. This check does not fire
on sprint, design, triage, or any other status.

## Output format

```
audit-ticket — <ticket-id>
Verdict: PASS | AMEND | SPLIT | DISCARD
Build-tightness: tight | medium | loose

Checks passed: <N>  
Findings:
- [duplicate] <T-xxx already covers this>
- [validation-steps] runtime observation not specified
- [validation-remediation] test artifact cleanup not specified
- [rollback-plan] HIGH-inertia touch without rollback plan
- [logging-required] try/except at <location> needs ERROR log
- [observability] no observable log line named
- [split] 3+ verbs in one ticket (proposed: T-a + T-b)
- [audit-emphasis] <tag or "none">
- [feedback-loop] skills/<name>/ missing: <property list> (advisory — does not block)
- [hold-gate] status=hold but no named dependency (T-xxx) or Akien: action found

Amended ticket (if AMEND): <diff from input>
Child proposals (if SPLIT): <list>
```

## Challenge (always — advisory, never blocks verdict)

After all checks, always ask:

```
CHALLENGE: Is there a simpler implementation that achieves the same goal?
  - Could the scope be cut further without losing the KR impact?
  - Is there an existing primitive (tool, table, skill) that makes this ticket
    unnecessary or half the size?
  - If this ticket fails, what's the next simplest thing that would still move the needle?
```

Output labeled `CHALLENGE:` — never blocks PASS, AMEND, SPLIT, or DISCARD.
This fires on every audit-ticket run, no exceptions.

## Hard rules

- Always run checks 1–8 first, then checks 9–16.
- AMEND on missing validation steps — "tests pass" is not a runtime validation.
- SPLIT when verb count ≥ 3 in a ticket > S size.
- SPLIT capability tickets that ship only one side (handler XOR skill
  consumer) — unless the ticket carries an explicit two-sided-build
  exemption line with a stated reason.
- HIGH-inertia rollback plan is required — ask Akien if unclear.
- Emit per-run telemetry:
  `from lab.claudecode.audit_telemetry import emit_run_record, AuditRunRecord`
- Hold-status tickets without a named dependency (T-xxx) or Akien: action → AMEND, no exceptions.
