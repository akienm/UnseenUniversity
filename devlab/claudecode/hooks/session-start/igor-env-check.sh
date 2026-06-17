#!/usr/bin/env bash
# intent: shell script: igor-env-check.sh
# igor-env-check.sh — Claude Code SessionStart hook.
#
# Validates that the Igor Postgres instance is reachable before the session
# begins. Delegates to `igor-admin env --check` (which asserts UU_HOME_DB_URL
# is set AND runs a trivial SELECT 1 against the DB).
#
# Non-fatal: always exits 0 so CC can still start even if the DB is down. The
# WARNING is surfaced to stderr so Akien sees it before wasting a session.
#
# Wired in ~/.claude/settings.json under hooks.SessionStart.
#
# Ref: T-cc-hook-autonomics

set -u

# Derive repo root from CC_WORKFLOW_TOOLS (two levels up) or fall back
if [[ -n "${CC_WORKFLOW_TOOLS:-}" ]]; then
    REPO_ROOT="$(dirname "$(dirname "${CC_WORKFLOW_TOOLS}")")"
else
    REPO_ROOT="${IGOR_REPO_ROOT:-$HOME/dev/src/UnseenUniversity}"
fi
IGOR_ADMIN="$REPO_ROOT/devlab/claudecode/igor_admin.py"

if [[ ! -f "$IGOR_ADMIN" ]]; then
    printf '\033[33m[igor-env-check] SKIP: %s not found\033[0m\n' "$IGOR_ADMIN" >&2
    exit 0
fi

# Capture output so we can prefix and loudly flag failures.
out="$(python3 "$IGOR_ADMIN" env --check 2>&1)"
rc=$?

if [[ $rc -eq 0 ]]; then
    # Success: quietly echo to stderr so CC notices but it's not intrusive.
    printf '[igor-env-check] OK: %s\n' "$out" >&2
    exit 0
fi

# Failure: loud red banner.
printf '\033[31m' >&2
printf '================================================================\n' >&2
printf ' IGOR ENV CHECK FAILED (rc=%d)\n' "$rc" >&2
printf '%s\n' "$out" >&2
printf ' Session will continue, but DB-backed operations will fail.\n' >&2
printf '================================================================\n' >&2
printf '\033[0m' >&2

# Non-fatal per ticket spec.
exit 0
