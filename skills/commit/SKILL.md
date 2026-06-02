---
name: commit
description: Thin commit cycle — test, stage, commit, pull, push. For ad-hoc commits outside sprints.
model: haiku
---

# /commit — Test + ship

For commits outside of /sprint (one-off fixes, doc edits).

## Steps

1. **Check gh auth**:
   ```bash
   gh auth status 2>&1
   ```
   If expired: STOP, tell Akien.

2. **Test** (from repo root):
   ```
   uv run pytest tests/ -x -q
   ```
   Fail = STOP.

3. **Review diff**:
   ```bash
   git diff --stat && git diff
   ```
   No secrets, no `.env`, no runtime data.

4. **Stage specific files** (never `git add -A`):
   ```bash
   git add <file1> <file2>
   ```

5. **Commit + push**:
   ```bash
   git commit -m "$(cat <<'EOF'
   feat/fix/docs: description

   Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
   EOF
   )"
   git pull --rebase origin main && git push origin main
   ```

## Hard rules
- Hooks run on every commit; pushes are non-force on main (integrity preserved).
- Stage files specifically by name — keeps `.env`, `*.db`, and `~/.unseen_university/` runtime paths off the commit.
- Tests pass + no secrets = commit without asking.
