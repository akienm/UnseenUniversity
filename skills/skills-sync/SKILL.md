---
name: skills-sync
description: Sync skills between local (~/.claude/skills/) and the canonical repo. Repo→local overwrites managed skills; local→repo promotes a local-only skill into the canonical set.
model: haiku
---

# /skills-sync — Sync skills between local and repo

Two directions:
- **repo → local**: show what managed skills are present/missing, then deploy all of them.
- **local → repo**: show local-only skills (not in the manifest), offer to copy them back to the canonical source.

## Step 1 — Show current state

```bash
agentctl skills status
```

This prints:
- `managed` — skills in the manifest that deploy to this host
- `present` — skills currently in `~/.claude/skills/`
- `local-only` — present locally but NOT in the manifest (user-added)
- `NOT deployed` — managed but not yet present locally

## Step 2 — Find the canonical repo path

```bash
python -c "from devices.installer import DEFAULT_MASTER_ROOT; print(DEFAULT_MASTER_ROOT)"
```

Call this `$MASTER_ROOT`. It resolves at runtime from the installed package — no hardcoding.

## Step 3 — Check what would change (repo → local)

For each managed skill present on both sides, show content differences:

```bash
diff -rq "$MASTER_ROOT" ~/.claude/skills/ \
  --exclude=".*" \
  2>/dev/null | grep -v "^Only in ~/.claude"
```

If there are differences, list them. If none, say "all managed skills are up to date."

Note: `agentctl skills deploy` uses `rsync --checksum --delete`, so **the repo always wins** for managed skills regardless of which side was edited more recently. Local edits to managed skills will be overwritten.

## Step 4 — Repo → local deploy (with consent)

Show a summary of what will change (skills with diffs + any NOT-deployed skills). Ask once:

> Deploy all managed skills from repo to local? (y/n)

On yes:
```bash
agentctl skills deploy
```

Report the `deployed:` count and any warnings from the output.

## Step 5 — Local → repo (reverse direction)

From the `agentctl skills status` output, identify `local-only` skills (present locally, not in the manifest).

If any exist, list them and ask once:

> Copy these local-only skills to the repo? (y/n)

On yes, for each skill `$NAME`:

```bash
rsync -a --delete ~/.claude/skills/$NAME/ "$MASTER_ROOT/$NAME/"
```

Then remind the user: **a new local-only skill copied to the repo also needs a manifest entry** or it won't deploy to other hosts. Show the entry to add to `skills/manifest.json`:

```json
"$NAME": {"category": "machine-agnostic", "machines": ["*"], "deploy": true}
```

After all copies are done, suggest running `agentctl skills deploy` again to confirm the round-trip.

## Hard rules

- Never add to manifest.json automatically — only show the snippet; the user decides.
- Consent is batch, not per-file. Ask once per direction, not once per skill.
- Always run `agentctl skills status` first so the user sees the starting state before anything changes.
