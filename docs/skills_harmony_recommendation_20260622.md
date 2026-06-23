# Skills Harmony — Recommendation (2026-06-22)

For T-skills-location-audit. Read before Friday's skills discussion.
Compares `~/.claude/skills/` (local, 60 skills — what actually executes) against
repo `skills/` (49 + `manifest.json` — the canonical master).

---

## TL;DR (REVISED 2026-06-23 after content inspection — read this version)

**The model is sound — keep it.** Repo `skills/` + `manifest.json` is the canonical
master; `/skills-sync` deploys repo→local ("master wins"); Igor's runtime-debug skills are
*deliberately* local-only.

**But the drift is NOT a simple newer/older — and `deploy` is NOT safe yet.** Content
inspection (not mtime) shows **both trees are inconsistently half-migrated across four
independent axes**, and neither tree is consistently forward on all of them:

| Axis | stale form | forward form |
|---|---|---|
| palace paths | `theigors/...` | `unseenuniversity/...` |
| design command | `/decided` | `/sorted` |
| decision storage | `decisions_log.dsb` | `devlab/runtime/memory/` store |
| repo / runtime root | `~/TheIgors`, `~/.TheIgors` | `~/dev/src/UnseenUniversity`, `~/.unseen_university` |

Example: repo `audit-audits` has the new `unseenuniversity/` paths but still says
`/decided`; local has `/sorted` but the old `theigors/` paths. So a blind `deploy`
(repo→local, master-wins, all-or-nothing) would **regress the live skills** — e.g. push
`/decided` naming back over the working `/sorted` versions. A blind sync *either direction*
loses something.

**Correct reconciliation = a per-axis MERGE to one forward-canonical version of each of the
19 drifted skills, committed to repo, THEN a deliberate verified deploy.** That is genuine
review/session work, not a mechanical sync. (The env-var axis `IGOR_HOME_DB_URL →
UU_HOME_DB_URL` is intentionally left to the gated de-hardcoding sweeps `T-uu-sweep-*`,
which cover `skills/` too — don't hand-edit env vars here.)

**Per-skill calls that changed after inspection:**
- `sorted` → **keep LOCAL** (its `decisions_log.dsb` append): `T-decisions-dsb-cutover` is
  still OPEN and `context-load` still reads the `.dsb` fallback. Dropping the write half
  (repo's version) is a partial-migration fake-completion.
- The 13 "repo-newer" skills are mostly **stale on content** (old `theigors/`, `/decided`,
  `~/TheIgors`) despite newer mtime — local is forward on naming, repo on some paths. Each
  needs a merge, not a side-pick.

### Done 2026-06-23 (safe, zero-deploy, committed)
- Deleted dead local skills `ADCHelp` (→`/workflow`) and `decided` (→`/sorted`); removed the
  stale `decided` manifest entry (it pointed at a skill absent from the repo).
- Promoted `query-ticket`, `mytickets`, `opentickets`, `concept` local→repo + manifest
  (additive; canonical; not deployed).

### Deferred to the review (the real work)
- The **19-skill per-axis merge** to forward-canonical, then a verified `/skills-sync deploy`.
- `research` / `critic` / `factory-create` sit in the repo dir but **unmanaged** (no manifest
  entry) — productizing them (manifest `deploy:true`) is a deliberate decision, not drift
  cleanup. Review item.
- `new-agent` is `deploy:true` but missing locally — deploy will place it once deploy is safe.

---

## The intended model (the good Friday story)

- `skills/manifest.json` lists every managed skill with `deploy: true|false` and `machines`.
- `/skills-sync` (haiku) syncs both ways: **repo→local** overwrites managed skills
  ("master wins"); **local→repo** promotes a local-only skill into the canonical set.
- Manifest note: *"igor-internal skills live in `~/.claude/skills/` on Igor machines only"*
  — so the Igor debug skills being local-only is **by design**, not drift.

→ Framing: *"Skill set is coherent by design — manifest-driven, repo-canonical, Igor-debug
skills intentionally local. Current drift is an un-run sync plus a few un-promoted local
skills."*

---

## Buckets & recommended calls

### A. DELETE — confirmed stale/superseded (2)
| Skill | Why | Action |
|---|---|---|
| `ADCHelp` | Replaced by `/workflow` (your confirmation). Not in manifest. | `rm -rf ~/.claude/skills/ADCHelp` |
| `decided` | Renamed to `/sorted` long ago (the canonical name). | Remove local dir **and** the stale `"decided"` entry still in `manifest.json` (currently `deploy:true`). |

### B. RECONCILE LOCAL→REPO **FIRST** — local is newer, do NOT blind-sync (6)
Diff each, promote the correct version into the repo before any deploy. Sizes:
| Skill | changed lines | note |
|---|---|---|
| `autocompact` | 48 (local 59 vs repo 89) | ⚠️ local is the trimmed post-regression version; repo looks like the **old bloated** one. Promoting local **prevents a regression**. |
| `day-close` | 37 | most-used; don't lose local edits |
| `sprint-ticket` | 22 | core sprint unit |
| `sorted` | 15 | core design-close |
| `day-close-audit` | 14 | |
| `design` | 11 | |

### C. PROMOTE LOCAL→REPO — canonical, machine-agnostic, missing from repo (3 + 1)
| Skill | Why |
|---|---|
| `query-ticket` | CLAUDE.md names it THE canonical "what's next" entry point — **must** be in the canonical set. |
| `mytickets` | Distinct view: tickets needing Akien's hands (role=guru/worker=akien). Not a dupe. |
| `opentickets` | Distinct view: everything in-flight with routing. Not a dupe. |
| `concept` | Evaluate — promote if still used, else delete. |

### D. KEEP AS LOCAL-ONLY — Igor-internal, by design (9 + 1)
`cognition-debug`, `debug`, `debug-pe-chain`, `diagnose`, `igor-diagnose`, `map-igor`,
`readigor`, `health`, `recall` — manifest says Igor-runtime skills live only on Igor
machines. Coherent; leave alone.
(`deep-audit` is `deploy:false` in the manifest — tracked-not-deployed; fine as-is.)

### E. DEPLOY REPO→LOCAL — in canonical set, absent locally (3 + 1)
`new-agent` (`deploy:true`), `research`, `critic` — present in repo, missing locally;
deploy them. (`factory-create` — confirm you want it on this box before deploying.)

### F. SYNC THE REST repo→local — repo-newer drift, safe to overwrite (13)
`audit-audits, commit, context-load, export-chat, fixit, note, outcome, question,
readinbox, sprint, sprint-batch, test-fix, workflow` — repo is newer; a normal
`/skills-sync` deploy updates local. No reconciliation needed.

---

## Recommended sequence (fold into tomorrow's day-close)

1. **Delete** `ADCHelp` + `decided` dirs; drop `decided` from `manifest.json`.
2. **Bucket B (6):** `diff` each local↔repo, copy the chosen (usually local) version into
   repo `skills/`, commit. This is the load-bearing step — do it before any deploy.
3. **Bucket C (3–4):** copy local→repo, add manifest entries, commit.
4. **Bucket E (3–4):** deploy repo→local; verify they appear.
5. **Run `/skills-sync`** full deploy to flush bucket F.
6. **Re-run the drift audit** (the `comm` + per-skill `diff` from this ticket) → expect zero
   drift and a manifest that matches both trees.

## Counts
Local 60 · Repo 49 (+manifest) · Shared 44 (**19 drifted**: 13 repo-newer, 6 local-newer)
· Local-only 16 (9 keep-as-Igor, 2 delete, 3–4 promote, 1 evaluate, 1 deep-audit)
· Repo-only 4 (deploy).
