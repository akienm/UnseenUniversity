---
name: audit-precode
description: Pre-edit code quality audit. Invoked by /sprint after plan-review (step 4.5), before the first edit. Catches hallucinated targets (P1 bug context): verifies every file and symbol in the plan exists; gates HIGH-inertia touches; checks for deprecated forms from preferred_paths; validates test plan and docstring plan. Returns PASS / AMEND. Model: Haiku (escalates to Sonnet on HIGH-inertia).
---

# audit-precode — Pre-edit validation

The cheapest place to catch a wrong plan. Runs after plan-review, before
any file is touched. A mismatch here costs one sentence; the same mismatch
found in tests costs an hour.

## Input

The sprint plan (3-sentence description from /sprint step 4) plus the ticket
description. Both are available in conversation context.

## Checks (in order)

### 1. File existence
For every file path named in the plan:
```bash
ls <path> 2>/dev/null || echo "MISSING: <path>"
```
Missing file = AMEND. The plan must name real files.

### 2. Symbol existence
For every named function, class, or constant the plan proposes to call or
modify:
```bash
grep -rn "<symbol>" unseen_university/ devices/ 2>/dev/null | head -5
```
Symbol not found = AMEND. "I'll add it" is fine; "I'll modify it" on a
non-existent symbol is not.

### 3. HIGH-inertia reaffirmation
When the plan touches `brainstem/`, `memory/models.py`,
`cognition/reasoners/base.py`, or any file flagged HIGH-inertia:
- Require inline Akien pre-approval BEFORE this check passes.
- Stamp reason: "pre-approved by Akien YYYY-MM-DD for touching <file>"
- "The ticket said so" is NOT a reaffirmation. Produce the reason.

### 4. Preferred-paths check
Load preferred pairs from palace:
```
memory_get(path="unseenuniversity/rules/preferred_paths")
```
Then check each child node's `deprecated` field against the plan text.
Match → AMEND with preferred alternative.

Patterns to watch for even without the rules store loaded:
- `psycopg2.connect` for memory reads → read the flat-file store (`devlab/runtime/memory/`)
- `from devlab.claudecode.channel import` → use `_post_to_channel` or MCP
- `print(` in device code → use `self.log.*`
- new `MemoryType.` enum value → metadata tag instead
- new `IGOR_*_ENABLED` flag → build to intent + go-live-when ticket

### 5. Test plan named
The plan must name:
- Which test file(s) will be created or modified
- Which test cases will cover the new behavior
"Tests will be added" without specifics = AMEND.

### 6. Docstring plan (load-bearing touches)
When the plan modifies a load-bearing file (any file with `Updated YYYY-MM-DD`
in its module docstring, or any file in `brainstem/`, `memory/`, `cognition/`):
- Plan must name updating the `Updated` timestamp in the docstring.
"I'll update the docstring" = PASS. Silence = AMEND.

### 7. Diff-size estimate vs ticket size
Estimate the number of lines that will change. Compare to ticket size:
- S: expect ≤ 100 lines
- M: expect ≤ 300 lines
- L: expect ≤ 800 lines

Mismatch > 2× declared size = flag. "This looks like an M plan for an S ticket."

## Output

```
audit-precode — <ticket-id>
Verdict: PASS | AMEND

Checks passed: <N>
Checks flagged:
- [missing-file] <path> not found
- [missing-symbol] <symbol> not found in codebase
- [high-inertia] <file> needs explicit pre-approval
- [preferred-path] plan uses deprecated form X → prefer Y
- [test-plan] no test cases named
- [docstring-plan] load-bearing file touched but docstring update not named
- [size-mismatch] plan looks ~<N>× larger than declared ticket size <S/M/L>
```

## Hard rules
- Always run check 1 (file existence) even when the plan is short — the P1
  bug is Sonnet confidently naming a file that doesn't exist.
- HIGH-inertia reaffirmation requires a human reason, not a ticket cite.
- AMEND on any failed check — don't skip and proceed.
- Emit per-run telemetry at the end of every run (even PASS):
  `from devlab.claudecode.audit_telemetry import emit_run_record, AuditRunRecord`
