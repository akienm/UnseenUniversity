# Skills — Intention Review (2026-06-23)

For T-skills-location-audit, BEFORE finalizing the single-source proposal. Reviews every skill
through two intentions, not by which copy is newer.

- **Intention #1 — implement the WORKFLOW.** Does the skill drive a pipeline phase, and does it
  need *reasoning*? If yes → it's a skill.
- **Intention #2 — let Akien see what CC sees WITHOUT an inference call.** If it just surfaces
  state (read-only, no judgment) → it should be a zero-inference `uu` CLI command, not a skill.
  Packaging a pure view as a skill spins up CC just to look at state — backwards. `mytickets` is
  the canonical example.

Sorting key: **needs reasoning → KEEP-SKILL. just surfaces state → →uu-CMD.**
Other verdicts: **MERGE** (fold into another), **DELETE** (dead/deprecated), **IGOR** (Igor-runtime,
stays local-only tier), **FIX-TEXT** (stale content to repair in the one-time merge).

---

## A. VIEW layer → `uu` CLI (Intention #2) — zero-inference, run from the terminal
These already wrap existing scripts (cc_queue.py, stall_check.py, cc_inbox.py, channel.py, uurecall/
uuresearch). Promote them to first-class `uu <verb>` commands; keep a thin skill ONLY if CC calls
them mid-reasoning (and even then it shells out to the same script).

| skill | what it shows | verdict |
|---|---|---|
| `mytickets` | tickets needing Akien (role=guru/worker=akien) | →uu-CMD (`uumytickets`) |
| `opentickets` | everything in-flight + routing | →uu-CMD (`uuopentickets`) |
| `query-ticket` | the next available ticket | →uu-CMD + CC shells out (canonical "what's next") |
| `stall-check` | in_progress tickets past 2h | →uu-CMD (`uustall`) |
| `health` | Granny health: sessions, queue depth, orphans | →uu-CMD (`uuhealth`) |
| `readinbox` | unread CC inbox (jsonl) | →uu-CMD (`uuinbox`); mark-read is a write, still no inference |
| `readigor` | Igor's recent channel replies | →uu-CMD (`uureadigor`) |
| `recall` | multi-source search (logs/tickets/code/palace) | →uu-CMD (`uurecall` already exists) |
| `research` | knowledge-base search | →uu-CMD (`uuresearch` already exists) |
| `map-igor` | Igor full-state snapshot to a file | →uu-CMD (`uumap`) |
| `export-chat` | dump session transcript | →uu-CMD (`uuexport`) |
| `workflow` | 30-sec static reference map | →uu-CMD or a doc (no reasoning) |
| `notify` | notification settings status/set | →uu-CMD (control, no inference) |
| `available` | reset Granny availability flag | →uu-CMD (control, no inference) |

→ This is the bulk of the "make it easier to see what you see" win: **14 skills become a `uu` CLI**,
and you stop paying tokens to look at your own state.

## B. WORKFLOW skills → KEEP (Intention #1) — genuinely need reasoning
| skill | phase | note |
|---|---|---|
| `context-load` | orient | KEEP (assembly + judgment); the heavy reads could call the uu CLI |
| `recover` | orient (post-rewind) | KEEP |
| `goal` | design | KEEP |
| `design` | design (open block) | KEEP — FIX-TEXT (says /decided) |
| `sorted` | design (close→tickets) | KEEP — canonical (not /decided) |
| `ticket` | capture | KEEP |
| `question` | capture (parking lot) | KEEP |
| `concept` | capture (declarative ref) | KEEP |
| `note` | capture (non-ticket log) | KEEP (borderline mechanical) — FIX-TEXT |
| `migrate-decisions` | design post-step | KEEP (mechanical; could be uu-CMD) |
| `sprint-ticket` | build (atom) | KEEP — the core unit |
| `sprint-batch` | build (multi) | KEEP |
| `sprint-loop` | build (autonomous) | KEEP |
| `fixit` | build (sorted+batch) | KEEP — FIX-TEXT (says /decided) |
| `sprint` | build (wrapper) | **MERGE** → ≈ `query-ticket + sprint-ticket`; fold or drop |
| `savestate` | close | KEEP (mostly mechanical) |
| `day-close` | close (orchestration) | KEEP — FIX-TEXT (stale slate path, ceremony tooling) |
| `autocompact` | close | KEEP — local copy is canonical (repo is the bloated regressed one) |
| `outcome` | review | KEEP — FIX-TEXT |
| `eval-run` | review (Friday) | KEEP |
| `weekly-retro` | review (Friday) | KEEP |
| `commit` | util (ad-hoc) | KEEP (mechanical) |
| `test-fix` | util | KEEP — FIX-TEXT ("TheIgors project") |

## C. AUDIT layer → KEEP but RUN A PROPORTIONALITY PASS (Intention #1, 14 skills)
Proof-on-close is now the verification spine. Re-justify each audit's slot against it.
- Filing gates: `audit-design`, `audit-hypothesis`, `audit-goal`, `audit-ticket` — KEEP (gate /sorted).
- Per-sprint gates: `audit-precode`, `audit-smell`, `audit-debris`, `audit-regression` — KEEP, but
  check overlap with proof-on-close + the day-close audit.
- Cadence/meta: `audit-day`, `audit-audits`, `audit-expert`, `audit-feedback`, `audit-workspace`,
  `deep-audit` — KEEP weekly/monthly; candidates to CONSOLIDATE (much overlap; heavy).

## D. IGOR-runtime → local-only tier (stay real dirs, not in canonical repo set)
`diagnose`, `debug`, `dream`, `igor-diagnose` — KEEP as Igor-box-local (manifest: machines:[], deploy:false).
Note `igor-diagnose` may overlap `diagnose` — check for a MERGE.

## E. DELETE — dead/deprecated
| skill | why |
|---|---|
| `cognition-debug` | self-labeled "DEPRECATED — use /diagnose igor" |
| `debug-pe-chain` | self-labeled "DEPRECATED — use /diagnose igor" |
| (`ADCHelp`, `decided`) | already deleted 2026-06-23 |

## F. SCAFFOLDING — KEEP (repo)
`new-agent`, `factory-create`, `critic`, `skills-sync` (role shrinks under single-source).

---

## Synthesis — how this reshapes the single-source proposal

The two intentions split the set cleanly into **two products**, and that split should drive the
storage model:

1. **`uu` CLI (Intention #2)** — ~14 view/control commands. Zero-inference, run from the terminal,
   CC shells out to the same scripts. Lives in the repo (`devlab/claudecode` / `bin/`). This is the
   biggest "see what you see" win and removes 14 things from the skill set entirely.
2. **Workflow skills (Intention #1)** — the reasoning-bearing pipeline + audit + scaffolding skills.
   THESE are what the single-source/redirect proposal applies to: canonical in the repo
   (project-scope `.claude/skills/` or symlink), Igor-runtime stays local-only.

Net counts: ~60 skills → delete 2 (already 4 with ADCHelp/decided), convert ~14 to `uu` CLI, merge
~2 (sprint, maybe igor-diagnose), consolidate the audit cadence layer → a **much smaller, coherent
skill set** that is exactly "the things that need reasoning," plus a separate zero-inference CLI for
"the things you look at." Do the content merge once on the survivors, THEN flip to single-source.
