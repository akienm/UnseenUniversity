---
name: audit-debris
description: Post-test pre-commit cleanup audit. Folds /validate-files. Checks for temp files, debug artifacts, staged runtime paths, uncommunicated .env changes, log-size growth, test DB leftovers, file placement, doc rot on changed load-bearing files, and subsystem_index drift. Returns PASS / AMEND. Model: Haiku.
model: haiku
---

# audit-debris — Pre-commit cleanup

The last mechanical gate before `git add`. A green test suite doesn't mean
a clean diff. This skill finds the debris that tests can't see.

## When to run

After `python -m pytest tests/ -x -q` passes, before `git add`. Also
callable at PR time for a full sweep.

## Checks (in order)

### 1. Temp/artifact files
```bash
find . -name "*.tmp" -o -name "*.bak" -o -name "*.swp" \
       -o -name "*.pyc" -o -path "*/__pycache__/*" \
       2>/dev/null | grep -v ".git" | head -20
```
Any `.tmp`/`.bak`/`.swp` file = AMEND. `__pycache__`/`.pyc` = note (not blocking, .gitignore should catch).

### 2. Runtime files staged
```bash
git diff --staged --name-only | grep -E "^~/.unseen_university/|\.db$|\.env$"
```
Any runtime path or `.db` file staged = AMEND (always stage by name, not `git add -A`).

### 3. .env changes
```bash
git diff --staged -- .env wild_igor/.env 2>/dev/null
```
Any `.env` change = AMEND unless commit message explicitly notes the change and reason.
Rule: always note what changed and why when editing `.env`.

### 4. Debug artifacts
```bash
git diff --staged | grep -E "^\+.*\b(print\(|breakpoint\(\)|import pdb|pdb\.set_trace)"
```
Any debug statement added = AMEND.

### 5. Log-size growth check
For each new log call added, verify it's behind a level check or a
condition — unbounded `log.debug(big_object)` in a tight loop causes
runaway log growth.

### 6. Test DB cleanup
```bash
# Verify test isolation — no rows left in test schemas
psql $UU_HOME_DB_URL -c "
  SELECT schemaname, tablename, n_live_tup
  FROM pg_stat_user_tables
  WHERE schemaname LIKE 'test_%'
    AND n_live_tup > 0
" 2>/dev/null | head -20
```
Live rows in `test_*` schemas = AMEND (conftest teardown missed them).

### 7. File placement
```bash
python3 ${CC_WORKFLOW_TOOLS}/validate_files.py 2>/dev/null | head -20
```
Any misplaced file = AMEND. Code under `wild_igor/`, runtime under `~/.unseen_university/`.

### 8. Docstring rot (load-bearing touched files)
```bash
git diff --staged --name-only | xargs -I{} grep -l "Updated [0-9]" {} 2>/dev/null
```
For each load-bearing file in the staged diff: verify the `Updated` timestamp
was advanced to today. A changed load-bearing file with a stale `Updated` date
= AMEND.

### 9. Subsystem_index drift
When the staged diff includes a move or rename of a primary file listed in
`unseenuniversity/subsystem_index`:
```
memory_get(path="unseenuniversity/subsystem_index")
```
If the renamed file appears in the index, update the index node. Else: note.

### 10. Commented-out code
```bash
git diff --staged | grep -E "^\+\s*#.*=|^\+\s*# [a-z].*\(|^\+\s*#.*import" | head -10
```
Large blocks of commented-out code = AMEND (delete it; git history is the
undo).

## Output

```
audit-debris — <ticket-id or "pre-commit">
Verdict: PASS | AMEND

Checks passed: <N>
Checks flagged:
- [temp-files] <list>
- [runtime-staged] <list>
- [env-undocumented] .env changed without commit message note
- [debug-artifact] print()/breakpoint() in diff
- [log-growth] unbounded log call in tight loop
- [test-db-rows] <schema>.<table> has <N> live rows
- [misplaced-file] <file> in wrong location
- [docstring-stale] <file> Updated date not advanced
- [subsystem-drift] <old-path> renamed but index not updated
- [commented-code] large commented block in diff
```

## Hard rules
- Runs after tests pass — don't skip because "tests are green."
- AMEND on any temp file, debug artifact, or runtime path staged — these are
  never acceptable in a commit.
- Emit per-run telemetry:
  `from lab.claudecode.audit_telemetry import emit_run_record, AuditRunRecord`
