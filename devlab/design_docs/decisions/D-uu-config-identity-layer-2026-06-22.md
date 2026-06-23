# D-uu-config-identity-layer-2026-06-22

**title:** Collapse the four hardcoded identity constants into a config layer + resolvers, then rotate
**date:** 2026-06-22
**status:** open
**spawned_tickets:** T-uu-config-profile-layer, T-uu-identity-resolvers, T-uu-sweep-db-connection-string, T-uu-sweep-instance-name, T-uu-sweep-hostname, T-uu-env-coverage-audit, T-uu-rotate-db-password, T-uu-rename-role-and-db, T-consequence-uu-config-identity-layer

## Decision narrative

Four identity constants leaked across the repo: `Igor-wild-0001` (instance/DB name, ×238 files),
`choose_a_password` (DB password, ×155), `igor` as the DB role (×149), and `akiendelllinux` (machine
name, ×35) — committed credentials and per-machine hardcoding both. Collapse them into a config layer:
a LOCAL bootstrap file `~/.unseen_university/uu_bash_profile.sh` (the only plaintext secret on disk;
sets ~5 bootstrap vars: UU_DB_USER, UU_DB_PASSWORD, UU_DB_IP, UU_HOME_DB_IP, IGOR_NAME) which sources a
REPO-tracked, secret-free post-processor `bin/uu_bash_profile_processor.sh` that composes
`UU_HOME_DB_URL`, sets `IGOR_SWARM_NAME=$(hostname)`, and pulls the rest from the `vault` device
(fail-soft, cached). Code then reads identity through resolvers that resolve at CALL time and
raise-if-unset (no baked fallback, no import-scope crash). Finally rotate: the DB password is the real
neutralizer (kills the 155-file + git-history exposure in one `ALTER ROLE`); the role rename
(igor→akien, the employer) and DB rename (→`${IGOR_NAME}`) are decoupled "do it right" cleanup,
sequenced last and deferrable.

Key design choices: secrets store = the existing `vault` device (random Fernet master key, name-
independent → no crypto/data migration for the rename; already akien-owned). Owner role = `akien`
(literal — the vault already models it that way), carried per-install by `UU_DB_USER`. `IGOR_NAME` is
parameterized at its CURRENT value (`Igor-wild-0001`) so all sweeps are behavior-preserving refactors;
the value-change rides only in the deferrable rename ticket. `rescueclaude` stays orthogonal (no
injected credential — inherits env or starts without it, the recovery contract). New-module proof gap:
`bin/uu_bash_profile_processor.sh` will close shipped-unproven (T-emitter-new-module-proof).

## Intention

UU is a portable, secret-free substrate: credentials and identity (instance name, host, DB
role/password) are config a deployer supplies at the edge and resolved at runtime — never baked into
code or committed to the repo. A new install boots from one local file.

## Why

Committing credentials is a security violation that also normalizes the pattern (patient zero: the
`vault` device leaks its own bootstrap cred as a hardcoded fallback). Hardcoded instance/host names
block a clean new-install and a second machine. Both undercut the portable-erector-set north star.
CP6 — build safety as we go; CP4 — suck less for every install, not just this box.

## Followups

1. Vault / credential-manager maturation — the store the post-processor pulls from.
2. The paused launcher reorg (`rescueclaude` + root launchers → `bin/`).
3. The `THEIGORS_HOME` `NameError` in the two repo `run` copies (note/run, sprint-ticket/run).
4. Skill catch-up (goals → Intention/Why/Followups) is **already tracked by the existing sprint ticket
   `T-skills-goals-to-intentions`** (rip-out: `/sorted` Q1 → "which intention", `## Goal Link` → `## Intention`,
   drop every G-xxx prompt + `links.goals` schema). Not a new followup — surfaced here for linkage only.

## Hypothesis

A fresh checkout on a new machine boots by editing one local file (~5 vars), and `git grep` for
`choose_a_password`, `Igor-wild-0001`, `akiendelllinux`, and `postgresql://igor:` in live
(non-template, non-throwaway-test) tracked files returns zero.

## Measurement Signal

Those four `git grep` counts hit 0 in live files; a new shell sourcing `uu_bash_profile.sh` →
`uu_bash_profile_processor.sh` populates `UU_HOME_DB_URL`/`IGOR_NAME`/`IGOR_SWARM_NAME` from the local
file + `$(hostname)` (no baked defaults); and all services still connect after the password rotation.

<!-- No "Goal Link" section by design: goals are retired in favor of Intention/Why/Followups
(T-skills-goals-to-intentions). This record uses the new triad above. -->

