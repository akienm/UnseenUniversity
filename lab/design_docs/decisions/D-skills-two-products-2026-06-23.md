# D-skills-two-products-2026-06-23
**title:** Skills are two products — a zero-inference `uu` CLI (views) and a single-source set of reasoning-bearing workflow skills
**date:** 2026-06-23
**status:** open
**spawned_tickets:** T-uu-cli-dispatcher, T-uu-readfeed, T-skills-views-retire-to-cli, T-skills-prune-deprecated-merge, T-skills-content-merge-survivors, T-skills-single-source-flip, T-consequence-skills-two-products

## Hypothesis
Viewing system state (tickets, feeds, health, inbox) costs zero inference calls via a single `uu <verb>` CLI; the skill set holds only reasoning-bearing skills, single-sourced in the repo with no drift; ~13 view skills + readigor + 2 deprecated skills are gone.

## Measurement Signal
`uu mytickets` / `uu readfeed igor` run in a bare terminal with no CC process; `git grep` finds one editable copy per managed skill and zero invocations of retired view skills outside changelogs; the drift audit (comm + per-skill diff) returns zero; skill count ~60 → ~30.

## Goal Link
none: serves the two stated intentions directly — (1) skills implement the WORKFLOW coherently, (2) skills let Akien see what CC sees without an inference call. Goal layer retired in favor of intentions.

## Decision narrative
Reviewing the skill mess through two intentions (not by which copy is newer) revealed the set is actually **two products** glued into one folder:

1. **A zero-inference `uu <verb>` CLI** — the things you *look at*. ~14 "skills" are pure read-only state views that, as skills, cost an inference call to run (you spin up CC to see your own queue). They become a single `uu <verb>` dispatcher routing to the scripts that already back them (cc_queue.py, stall_check.py, cc_inbox.py, channel.py, uurecall/uuresearch); CC shells out to the same dispatcher. `uu` is free on this box (no collision). `readigor` generalizes into `uu readfeed <device> [channel]` (channels: public/personal/private/console/debug — mapping the Murderbot feed metaphor + the datacenter_logs hierarchy).

2. **A single-source set of reasoning-bearing workflow skills** — the things that *drive the agent*. These stay skills and become canonical in the repo via project-scope `.claude/skills/` (committed symlink to `skills/`; per-skill symlinks as fallback). `~/.claude/skills/` keeps only the Igor-runtime local-only tier (diagnose/debug/dream/igor-diagnose).

Ordering invariant (load-bearing): **merge before flip.** Both skill trees are inconsistently half-migrated across four axes (theigors→unseenuniversity paths, /decided→/sorted, decisions_log.dsb→filesystem store, ~/TheIgors→~/dev/src/UnseenUniversity). Flipping to single-source before a one-time per-axis content merge would freeze a stale-on-some-axis copy as canonical. So: retire views → prune deprecated/redundant → content-merge the final survivor set → THEN flip. (env-var axis IGOR_HOME_DB_URL→UU_HOME_DB_URL is left to the gated T-uu-sweep-* sweeps, which already cover skills/.)

**Explicitly deferred (NOT ticketed here):** the audit-layer proportionality pass — 14 audit skills vs proof-on-close as the new verification spine. Per Akien's "let's talk what's left on audits *if anything*," that conversation decides whether there's even a ticket; it is not pre-committed in this batch.

**Alternatives considered:** (a) keep two copies + run skills-sync — rejected: manages drift instead of eliminating it, and a blind deploy regresses live skills. (b) external skills-dir config — rejected: Claude Code has no `skillsDirectory` setting (confirmed); project-scope `.claude/skills/` is the supported no-copy path. (c) leave views as skills — rejected: violates the zero-inference-observability intention.

**Refs:** docs/skills_intention_review_20260623.md, docs/skills_harmony_recommendation_20260622.md. Rides on T-skills-location-audit.
