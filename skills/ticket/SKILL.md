---
name: ticket
description: Create or update a ticket. Args: "last" for thing just discussed, or description text.
model: haiku
---

# /ticket — Create or update a ticket

## Usage
- `/ticket last` — ticket whatever we just discussed
- `/ticket <description>` — create new or update existing

## Description template

Always shape the ticket `description` field this way — /audit-ticket checks for
these sections at filing time, and missing ones get flagged:

    <1-3 sentences: problem and proposed shape>

    **Affected files:** <comma-separated paths, or "TBD — discovery step in sprint" if genuinely unknown>
    **Design rules:** <which palace checks under unseenuniversity/rules/ticket_design_checks/ apply — e.g. "no-sqlite, test-plan-or-why-not". "none apply" only after thinking about it.>
    **Scope boundary:** <what's explicitly in scope; what's explicitly out of scope>
    **Test plan:** <specific tests to add or run, OR "no tests because: <reason>" — always state one or the other>
    **Proof obligation:** <what a hollow build could NOT pass — the obligation this ticket must discharge, threaded from the design sub-intention that produced it (proof-as-thread: intention → ticket → prereg → prove). build_packet surfaces it into proof_plan.proof_obligation. Omit only for a ticket with no design-borne obligation.>

Structure lives in description TEXT as labeled sections. Free-form narrative
on top, labeled fields below. The cc_queue.py DB row stays shape-stable —
the labeled fields go inside `description`, not into new columns.

## Steps

### 1. Identify: new ticket or update?

Always check for an existing ticket before drafting a new one — /audit-ticket's
duplicate check runs at filing time, but a pre-check here saves a round
trip.
```bash
python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/cc_queue.py list 2>/dev/null | grep -i "<keyword>"
```

### 2. Fill the structured fields

Always fill all four sections (Affected files, Design rules, Scope boundary,
Test plan) per the description template above. For `/ticket last`, infer
each field from the conversation; mark genuinely unknown fields as "TBD"
rather than skipping — /audit-ticket flags blanks, not TBDs-with-reason.

When the ticket touches memory shapes, read the relevant palace rule via:
```
memory_get(path="unseenuniversity/rules/memory")
```
And for persistence-touching work:
```
memory_get(path="unseenuniversity/rules/database")
```
Those reads surface the rule text the ticket needs to match; the result
feeds the "Design rules" field.

### 3. Review the plan before creating

Always state the plan back in one sentence before filing. Check:
- Inertia levels of affected files (read `memory_get(path='unseenuniversity/rules/safeguards')` if unsure)
- Scope boundary — is it tight?
- Test coverage — what specifically will be tested?

### 3.5. BuilderReport at filing (best-effort)

Call the classifier to get a BuilderReport and append it to the ticket description.
Non-fatal — if the classifier is down or returns empty, proceed without the report.

```bash
BUILDER_REPORT=$(python3 -m unseen_university.devices.classifier.cli classify \
  --title "<ticket title>" \
  --tags "<space-separated tags>" \
  --description "<ticket description>" 2>/dev/null) || BUILDER_REPORT=""
```

If `$BUILDER_REPORT` is non-empty and contains `"relevant_files"`, append to the description:
```
**Builder report:** <BUILDER_REPORT>
```

Skip silently when the output is `{}`, empty, or the `relevant_files` array is `[]`.

### 4. Create or update

Always use an ID of form `T-<kebab-slug>` (max 5 words). Check for collision
with existing ticket ids before creating.

New ticket:
```bash
# Write JSON to /tmp/ticket.json (matching queue.json schema), then:
python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/cc_queue.py add /tmp/ticket.json
```

Update existing ticket:
```bash
python3 ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/claudecode/cc_queue.py done|block|claim <id>
```

### 5. Add to slate

Always append the ticket ID to today's slate under `## Planned` or `## Ad hoc`
— otherwise the ticket lives only in the queue and the daily view misses it:
```bash
echo "- <id>: <title>" >> ${UU_ROOT:-$HOME/dev/src/UnseenUniversity}/devlab/runtime/memory/slates/$(date +%Y%m%d).slate.txt
```

### 6. Run /savestate

Always flush state after ticketing so the session record picks up the new
ticket id.
