---
name: audit-workspace
description: Workspace debris auditor — find orphaned directories, unused codebases, runtime artifacts. Flags candidates for deletion. Model: Haiku.
model: haiku
---

# audit-workspace — Debris detection for ~/dev/src and ~/.unseen_university

Finds directories that are either clearly obsolete, clearly unused, or unclear
in purpose. Combines filesystem heuristics (age, size, git history) with
codebase reference analysis (does anything import from it?).

## Invocation

```
/audit-workspace              # scan entire workspace
/audit-workspace --tier 1     # only definitely-delete candidates
/audit-workspace --tier 2     # only investigate-first candidates
/audit-workspace --fix        # suggest deletions (no auto-delete)
```

## Scans

### Scan 1: Directory age + git history

```bash
# Find dirs not modified in 90+ days
find ~/dev/src ~/TheIgors* -maxdepth 2 -type d \
  -mtime +90 2>/dev/null | while read dir; do
  if [ -d "$dir/.git" ]; then
    age=$(git -C "$dir" log -1 --format="%ai" 2>/dev/null | cut -d' ' -f1)
    echo "$dir (git: $age)"
  else
    age=$(stat -c %y "$dir" 2>/dev/null | cut -d' ' -f1)
    echo "$dir (fs: $age)"
  fi
done
```

### Scan 2: Codebase references

For each candidate directory:
```bash
# Does anything in UU import from this directory?
grep -r "from.*<dirname>\|import.*<dirname>" \
  --include="*.py" \
  /home/akien/dev/src/UnseenUniversity/ \
  2>/dev/null | wc -l
```

**0 references** = likely unused  
**≥1 reference** = in use, keep

### Scan 3: Directory naming heuristics

Flag directories matching:
- `.archive`, `-old`, `-backup`, `~bak`
- `TheIgors` (old project, now UU)
- `test-*`, `tmp-*`, `temp-*`

### Scan 4: Size (flag large unused dirs)

```bash
# Find directories >100MB not referenced
du -sh ~/dev/src ~/TheIgors* 2>/dev/null | awk '$1 ~ /G|M/ && $1+0 > 100'
```

### Scan 5: Runtime artifacts (in ~/.unseen_university)

Known ephemeral patterns:
- `.unseen_university/blobs/` — purpose unclear
- `.unseen_university/boot-smoke-test/` — what was this?
- `.unseen_university/run/` — temp runtime state

---

## Output Categories

### TIER 1: DEFINITELY DELETE

```
DIR: ~/TheIgors/
  Age: 300+ days (last commit 2026-02-15)
  Size: 2.3 GB
  References: 0
  Naming: matches .archive pattern
  → SAFE TO DELETE

DIR: ~/TheIgors.archive/
  Age: 300+ days
  Size: 1.8 GB
  References: 0
  → SAFE TO DELETE

DIR: ~/TheIgors-cert-walks/
  Age: 240+ days
  Size: 120 MB
  References: 0
  → SAFE TO DELETE
```

### TIER 2: INVESTIGATE FIRST

```
DIR: ~/AcuRite/
  Age: 180 days
  Size: 50 MB
  References: 0 (but unclear purpose)
  → Ask Akien: "created as part of AcuRite project but not used?"

DIR: ~/.unseen_university/blobs/
  Age: 40 days
  Size: 15 MB
  References: 0
  Purpose: Unknown
  → Ask Akien: "what is this?"

DIR: ~/.unseen_university/boot-smoke-test/
  Age: 60 days
  Size: 8 MB
  Purpose: Unclear
  → Ask Akien: "boot smoke test for what? still needed?"
```

### TIER 3: PROBABLY IN USE (VERIFY MANUALLY)

```
DIR: ~/dev/src/UnseenUniversity/lab/
  References: None (moved to devlab?)
  → Verify: is lab/ still needed or has it been replaced?

DIR: ~/TheIgorsProject/
  Age: Recent commits
  → Is this an active parallel project or stale?
```

---

## Hard Rule

**Never auto-delete without Akien approval.** This skill surfaces candidates,
Akien decides.

## Integration

- Run weekly (part of /day-close or standalone)
- Emit findings to telemetry
- File **T-cleanup-*-tier1** and **T-cleanup-*-tier2** tickets
- Tier 1 tickets (definitely delete) have high priority
- Tier 2 tickets require Akien approval before deletion

## Codebase Reasoner Integration

When available, use librarian/codebase-reasoner to answer:
- "Does anything reference ~/AcuRite?"
- "Does anything import from ~/agent_datacenter?"
- "What is the last recorded use of directory X?"

This provides confidence scores to supplement filesystem heuristics.
