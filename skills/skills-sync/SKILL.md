---
name: skills-sync
description: Sync skills between local (~/.claude/skills/) and the canonical repo. Repo→local overwrites managed skills; local→repo promotes a local-only skill into the canonical set.
model: haiku
---

# /skills-sync — Sync skills between local and repo

Two directions:
- **repo → local**: show what managed skills are present/missing, then deploy all of them.
- **local → repo**: show local-only skills (not in the manifest), offer to copy them back.

## Step 1 — Show current state

```
python run status
```

Shows: managed skills for this host, present locally, local-only (not in manifest),
and NOT deployed (managed but missing locally).

## Step 2 — Check what would change (repo → local)

```
python run diff
```

Shows skills whose content differs between master and local. Master always wins
on deploy — local edits to managed skills will be overwritten.

If none differ: "All managed skills are up to date."

## Step 3 — Repo → local deploy (with consent)

Show the diff summary. Ask once:

> Deploy all managed skills from repo to local? (y/n)

On yes:
```
python run deploy
```

Reports deployed count and any warnings.

## Step 4 — Local → repo (reverse direction)

From the status output, identify local-only skills (present locally, not in manifest).

If any exist, list them and ask once:

> Copy these local-only skills to the repo? (y/n)

On yes, for each `$NAME`:
```
python run copy-to-repo $NAME
```

The script copies the skill and prints the manifest.json entry to add. A new
local-only skill copied to the repo also needs a manifest entry or it won't
deploy to other hosts.

After all copies, suggest running deploy again to confirm the round-trip.

## Hard rules

- Never add to manifest.json automatically — only show the snippet; the user decides.
- Consent is batch, not per-file. Ask once per direction, not once per skill.
- Always run status first so the user sees the starting state.
