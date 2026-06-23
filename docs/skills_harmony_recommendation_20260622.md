# Skills Harmony — Recommendation (2026-06-22)

For T-skills-location-audit. Read before Friday's skills discussion.
Compares `~/.claude/skills/` (local, 60 skills — what actually executes) against
repo `skills/` (49 + `manifest.json` — the canonical master).

---

## TL;DR

**The model is already sound — keep it.** Repo `skills/` + `manifest.json` is the
canonical master; `/skills-sync` deploys repo→local ("master always wins"); Igor's
runtime-debug skills are *deliberately* local-only. The divergence is **operational
drift** (sync hasn't been run + a few local skills never got promoted), not a design gap.

**One real trap:** 6 drifted skills are **LOCAL-newer**. A blind repo→local sync would
**regress** them — most dangerously `autocompact` back to an old bloated version (the
exact 2026-06-17 regression its own guard comment warns about). Reconcile those repo-ward
*before* any deploy.

Cleanup is small: **2 deletions, ~6 local→repo promotions, 1 sync run.**

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
