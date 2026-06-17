# D-uu-root-env-var-2026-06-09
**title:** UU_ROOT env var — portable repo root with auto-detection fallback
**date:** 2026-06-09
**status:** open
**spawned_tickets:** T-uu-root-auto-detect, T-uu-root-migrate-skills, T-consequence-uu-root-env-var

## Decision narrative
Two canonical env vars: UU_ROOT (repo root) and IGOR_HOME (runtime, already exists). CC_WORKFLOW_TOOLS becomes a derived alias (${UU_ROOT}/lab/claudecode). A uu_root() utility auto-detects from package __file__ when UU_ROOT is not set, making fresh clones work without shell config. Skill run scripts migrated off hardcoded paths.

## Hypothesis
A fresh clone on any machine runs skill run scripts without setting CC_WORKFLOW_TOOLS or hardcoding a path.

## Measurement Signal
grep -r '/home/akien' skills/ returns nothing; python3 skills/savestate/run close 'test' works without UU_ROOT set.

## Goal Link
G-invisible-tools
