---
name: sorted
description: Batch-ticketize conversation decisions. Reads recent conversation turns (since /design marker or prior /sorted), summarizes each decision, drafts tickets per decision, runs /audit-ticket on each ticket filing-time, and writes to queue + slate + session record + Igor memory palace with two-way decision↔ticket backlinks.
model: sonnet
---

# /sorted — Close a design block → batch tickets

The closing mark of a design conversation. Takes "the stuff we just talked about" and makes it durable — decisions in the palace, tickets in the queue, everything linked.

## Inputs

- Optional arg: a brief one-line summary, e.g. `/sorted rename audit to day-close-audit`. If omitted, infer the summary from the scope.
- Scope boundary — look back to whichever is most recent:
  1. A `DESIGN_START` marker (written by /design), OR
  2. The most recent prior /sorted boundary, OR
  3. The session start.

## Steps

### 1. Determine scope

Always identify where the design block begins before drafting tickets — the
scope sets which turns feed each decision.

```
python -c "
from datetime import datetime; from pathlib import Path; import os, re, sys
slate = Path(os.environ.get('IGOR_HOME', Path.home()/'.unseen_university'))/'claudecode'/(datetime.now().strftime('%Y%m%d')+'.slate.txt')
if slate.exists():
    lines = [l for l in slate.read_text(encoding='utf-8').splitlines() if re.match(r'^(- D-|## In-flight|## Notes|DESIGN_START)', l)]
    print('\n'.join(lines[-20:]))
"
```

Pick whichever boundary appears most recently: DESIGN_START, prior /sorted,
or session start. When no prior boundary exists, treat the whole conversation
as scope.

### 2. Summarize the decision

Always write a one-to-two sentence summary and assign a decision id of the
form `D-<kebab-slug>-YYYY-MM-DD`. A decision without a D-id can't be
rolled up or traced back from the tickets it spawned.

### 2.6. Extract hypothesis (mandatory — 3 questions)

Always ask Akien these three questions before proceeding. They are not optional.
The hypothesis must be extracted and stored on the decision record before audit-design runs.

**Question 1:** "Which goal does this serve?" — answer with G-xxx, or explicit `none` with one-sentence reason.

**Question 2:** "What should be observably different after these tickets ship?" — the testable claim in plain English. One sentence.

**Question 3:** "How will we know? What's the signal?" — a metric, log line, behavior, or eval question that can be checked with current infrastructure.

Store answers in the decision record (both the .md file and palace node metadata):
```
## Hypothesis
<Question 2 answer>

## Measurement Signal
<Question 3 answer>

## Goal Link
<G-xxx or "none: <reason>">
```

If Akien can't answer Question 2 in one falsifiable sentence, the design may not be ready to ticket yet — surface that and offer to continue designing.

Then run `/audit-hypothesis` on the extracted hypothesis. If AMEND: apply fixes before proceeding to Step 2.5.

### 2.5. Audit the design (audit-design)

Always invoke `audit-design` on the decision summary + scope context before
drafting tickets. The audit runs nine positive checks (positive-target goal,
runtime-observable success, alternatives considered, constraints named,
"what am I missing" pass, conflicts with last-30d decisions, palace-rule
conflicts, scope decomposition, executor + inertia per piece) and returns:

- **PASS** → proceed to Step 3.
- **AMEND** → apply the listed amendments to the decision narrative (ask
  Akien if any are ambiguous), then re-run `audit-design`. Do not draft
  tickets until the audit returns PASS.
- **HIGH-inertia surface** → audit-design separately flags HIGH-inertia
  files mentioned in the narrative; pause for Akien pre-approval before
  proceeding, even on PASS.

Standalone re-check is supported via `/audit-design <decision-id>` after
the decision has been filed.

### 3. Draft tickets

For each implementation unit the decision implies, draft one ticket shaped
per the `/ticket` description template:
```python
{
  "id": "T-<kebab-slug>",
  "title": "<short title, <80 chars>",
  "size": "S|M|L|XL",
  "tags": ["<Topic>", "<Area>"],
  "description": "<problem + proposed shape + Affected files + Design rules + Scope boundary + Test plan>",
  "decision_id": "D-...",
  "gate": null,  # set if depends on another pending ticket
  "priority": 0.5  # raise for unblockers
}
```

### 3.5. Advisor review for L/XL drafts (D-sorted-advisor-probe-2026-06-06)

When any ticket in the current batch is L or XL size, call `advisor()` **before**
running /audit-ticket. This is the proactive Opus judgment step — cheap relative
to a sprint reset cycle.

**Trigger:** `size == "L" or size == "XL"` for at least one draft in the batch.
**Skip:** S-only batches — overhead exceeds benefit for small-scope tickets.

Call `advisor()` with the draft ticket(s) as context:

> "I'm about to file this L/XL ticket via /sorted. Review the completion criteria
> and scope boundary. Are the completion criteria machine-verifiable? Is the scope
> minimal? What am I missing?"

Apply any amendments the advisor returns — especially to **Completion criteria**
and **Scope boundary** — before proceeding to Step 4. If the advisor recommends
splitting the ticket, treat it as a SPLIT verdict and create child drafts.

Surface the advisor's key feedback inline as a one-line note before continuing.

### 4. Run /audit-ticket on each draft

Always invoke /audit-ticket once per drafted ticket — filing-time quality is the
whole point of /sorted. /audit-ticket returns one of:
- **PASS** → proceed to filing.
- **AMEND** → apply the amendments (ask Akien if ambiguous), re-submit.
- **SPLIT** → replace the single draft with N child drafts; run /audit-ticket on each.
- **DISCARD** → drop the draft; record the reason in the decision narrative.

When /audit-ticket flags a HIGH-inertia touch, always surface it inline for
Akien's pre-approval. Stamp the approval into the ticket body before filing
— that stamp survives compaction; CC's memory does not.

### 5. File the tickets

Write the post-review batch to `/tmp/sorted_batch_<decision-id>.json`, then
append to the queue:
```bash
python "${CC_WORKFLOW_TOOLS}/cc_queue.py" add /tmp/sorted_batch_<decision-id>.json
```
`cc_queue.py` is the canonical writer — always go through it so the slate
echo and session record stay consistent.

### 5.5. Draft consequence-check ticket (MANDATORY for M/L/XL; required for S with behavioral hypothesis)

**Mandatory for every M, L, or XL decision — no exceptions, no waiver.** Also mandatory for S-only decisions where Step 2.6 extracted a behavioral hypothesis (Q2 answer is present) or the narrative mentions MEDIUM+ inertia files.

**Skip only when:** every ticket in the batch is S AND no behavioral hypothesis was extracted in Step 2.6 AND the decision narrative mentions no MEDIUM+ inertia files. This is the only valid skip — it never applies to M/L/XL decisions.

**Gate field:** For M/L/XL or MEDIUM+ inertia decisions, use `<YYYY-MM-DD — 14 days from today>`. For S-only batches where the hypothesis triggered the ticket, set the gate to the ID of the last ticket in the batch (e.g., `T-<last-ticket-slug>`) so verification fires only after the batch ships.

Consequence ticket shape:
```python
{
  "id": "T-consequence-<D-slug>",
  "title": "Consequence check: <decision summary, ≤60 chars>",
  "size": "S",
  "tags": ["Consequence", "Workflow"],
  "description": (
    "**What changed:** <one sentence from the decision summary>\n"
    "**Predicted unintended effects:** <list from the pre-mortem pass — what could silently break, regress, or diverge>\n"
    "**Signals to watch:** <specific log lines, behavioral changes, or metric shifts to look for>\n"
    "**Gate condition:** Check by <YYYY-MM-DD 14 days from today> or when <observable event>.\n\n"
    "**Affected files:** None — observation and verification only\n"
    "**Design rules:** none apply\n"
    "**Scope boundary:** Observe predicted effects; annotate outcome as occurred / clear / partial and close\n"
    "**Test plan:** no tests — this is an observation ticket"
  ),
  "decision_id": "D-...",
  "gate": "<YYYY-MM-DD — 14 days from today>",   # compute with: python -c "from datetime import datetime,timedelta; print((datetime.now()+timedelta(days=14)).strftime('%Y-%m-%d'))"
  "priority": 0.3,
  "status": "sprint"
}
```

The "Predicted unintended effects" field must come from reasoning about the decision at filing time — not boilerplate. Ask: what implicit assumptions does this change make? What could regress silently? What adjacent subsystems depend on behavior this touches?

Run `/audit-ticket` on this draft before filing. File via `cc_queue.py add` alongside the batch (append to the same `/tmp/sorted_batch_<D-id>.json` file, or add separately — either is fine).

### 6. Write to Igor memory palace

Always create a decision node so the rollup loop can find it. Until
`T-decisions-into-palace-subtree` ships the palace writer, drop a file stub
at `lab/design_docs/decisions/D-....md`:
```markdown
# D-<id>
**title:** <one-line summary>
**date:** YYYY-MM-DD
**status:** open
**spawned_tickets:** T-x, T-y, T-z

## Decision narrative
<1-2 sentences from step 2 + context from the conversation scope>
```

Fields expected on the palace node (same shape):
- `title` — one-line decision summary
- `content` — decision narrative (summary + scope context)
- `spawned_tickets` — list of ticket ids created
- `date` — YYYY-MM-DD
- `status` — `open` (auto-closes when all spawned_tickets close, via decision-rollup)

### 7. Append to decisions log

Chronological append (this is the exception to "don't write to
decisions_log.dsb directly" — /sorted is a structured writer, not a blind
dump; the file becomes a generated echo once the palace migration ships):
```
python -c "
from datetime import datetime; from pathlib import Path; import os
ts = datetime.now().isoformat(timespec='seconds')
f = Path(os.environ.get("UU_ROOT", str(Path.home()/"dev/src/UnseenUniversity"))))/'lab'/'design_docs_for_igor'/'decisions_log.dsb'
f.parent.mkdir(parents=True, exist_ok=True)
f.open('a',encoding='utf-8').write(ts+' | D-... | <summary> | tickets: T-x, T-y, T-z\n')
"
```

### 8. Append to slate

```
python -c "
from datetime import datetime; from pathlib import Path; import os
slate = Path(os.environ.get('IGOR_HOME', Path.home()/'.unseen_university'))/'claudecode'/(datetime.now().strftime('%Y%m%d')+'.slate.txt')
slate.open('a',encoding='utf-8').write('- D-...: <summary> — T-x, T-y, T-z\n')
"
```

### 9. Clear /design flag (if set)

```
python -c "
from pathlib import Path; import os
f = Path(os.environ.get('IGOR_HOME', Path.home()/'.unseen_university'))/'cc_channel'/'design_mode.json'
f.unlink(missing_ok=True)
"
```

### 10. Report

```
/sorted <summary> — D-...
Tickets filed: T-x, T-y, T-z (<N> total)
All linked to D-... (two-way navigation via decision_id field + decision's spawned_tickets list)
```

## Flow integration

Design pattern:
```
/design (optional)
  → conversation turns (may include back-and-forth, questions, exploration)
/sorted <summary>
  → tickets filed, decision recorded, design block closes
/sprint-batch decision:D-...
  → sprints all tickets from this decision
```

Multiple decisions in one session:
```
/design
  → discuss topic A
/sorted A — T-a1, T-a2
  → discuss topic B
/sorted B — T-b1
  → discuss topic C
/sorted C — T-c1, T-c2, T-c3
/sprint-batch today-slate
  → sprints all 6 tickets across the three decisions
```

## Invariants

- Every decision gets a D-id, even single-ticket ones — makes trace navigable.
- Every ticket in a /sorted batch carries `decision_id` — no orphaned tickets.
- /audit-ticket runs on EVERY draft, not just the first or biggest.
- HIGH-inertia approvals land in the ticket body before filing; they are not kept in CC's conversational memory.
- Every M/L/XL decision — and every S-only decision where Step 2.6 extracted a behavioral hypothesis — gets a consequence-check ticket (Step 5.5). This is non-negotiable: no M/L/XL decision closes without one.
- Design status moves to `closed` only when: (a) all spawned_tickets are closed AND (b) at least one T-consequence-{slug} for this decision is also closed. Before writing `**status:** closed` to the decision .md file, verify: `python3 ${CC_WORKFLOW_TOOLS}/cc_queue.py list 2>/dev/null | grep "T-consequence"` for the decision slug shows a closed entry. If not, file the consequence ticket first.
- Any batch containing an L or XL ticket gets an `advisor()` review (Step 3.5) before /audit-ticket runs. S-only batches skip this.

## Hard rules

- Always run /audit-ticket on every drafted ticket — filing-time quality is the whole point.
- DISCARD verdicts from /audit-ticket block filing until Akien explicitly overrides.
- Every distinct decision gets its own D-id. Single-session doesn't mean single-decision.
- Decisions are append-only. New context becomes a new decision, linked via metadata.
- **Consequence-check ticket is MANDATORY for every M/L/XL decision.** No skip condition applies to M+ decisions. A design batch missing a consequence ticket is incomplete, not filed.
- **Design status only moves to `closed` when a consequence ticket for it is also closed.** Writing `**status:** closed` without a verified closed consequence ticket is a workflow violation.
