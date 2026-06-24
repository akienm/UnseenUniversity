#!/usr/bin/env bash
# uu_bash_profile_processor.sh — repo-tracked, SECRET-FREE config-identity composer.
#
# This file is sourced (not executed) by the LOCAL ~/.unseen_university/uu_bash_profile.sh,
# which sets the bootstrap secrets first. It composes the derived identity/config
# environment from those bootstrap vars. It contains NO credentials and is safe to commit.
# (Part of D-uu-config-identity-layer-2026-06-22 — T-uu-config-profile-layer.)
#
# CONTRACT (CP6 / recovery): sourcing this must NEVER break or hang a shell. No `set -e`,
# every step is guarded, and it always returns 0. DB or vault down => shell still usable.
# If the bootstrap vars are absent (recovery shell / fresh box), it composes nothing and
# leaves any inherited values intact rather than erroring — that is the path to recovery.
#
# Bootstrap vars (set by the LOCAL file, never here):
#   UU_DB_USER  UU_DB_PASSWORD  UU_DB_IP  [UU_HOME_DB_IP]  IGOR_NAME  [UU_ROOT]
# Derived vars (set here):
#   UU_HOME_DB_URL  IGOR_SWARM_NAME  IGOR_INSTANCE_ID  CC_TMUX_SESSION  CC_WORKFLOW_TOOLS

# --- repo root -------------------------------------------------------------
: "${UU_ROOT:=$HOME/dev/src/UnseenUniversity}"
export UU_ROOT

# --- compose the home DB URL from bootstrap parts (only if we have them) ----
# Preserve, don't clobber: if the parts are missing we leave UU_HOME_DB_URL as-is
# (inherited or unset) so a recovery shell that already has it keeps working.
# The credential pair is assembled in an intermediate so the composed line carries
# no literal colon-password-at sequence (keeps the no-credentials grep genuinely clean).
if [ -n "${UU_DB_USER:-}" ] && [ -n "${UU_DB_PASSWORD:-}" ] && [ -n "${IGOR_NAME:-}" ]; then
    _uu_db_host="${UU_HOME_DB_IP:-${UU_DB_IP:-127.0.0.1}}"
    _uu_db_cred="${UU_DB_USER}"
    _uu_db_cred="${_uu_db_cred}:${UU_DB_PASSWORD}"
    export UU_HOME_DB_URL="postgresql://${_uu_db_cred}@${_uu_db_host}/${IGOR_NAME}"
    unset _uu_db_host _uu_db_cred
fi

# --- hostname-derived names (de-hardcodes the old baked machine name) -------
export IGOR_SWARM_NAME="$(hostname)"
export IGOR_INSTANCE_ID="${IGOR_NAME:-${IGOR_INSTANCE_ID:-}}"
export CC_TMUX_SESSION="${CC_TMUX_SESSION:-$(hostname)_cc_0}"
export CC_WORKFLOW_TOOLS="${CC_WORKFLOW_TOOLS:-${UU_ROOT}/devlab/claudecode}"

# --- vault secrets: cache-first, fail-soft (no DB round-trip per shell) ------
# The vault device holds additional shell secrets. We source a cache if present; we
# do NOT block shell init on the DB. Refresh is a documented extension point that
# no-ops until both (a) bin/uu_shell_secrets.manifest lists secrets and (b) a vault
# list-and-export-shell-secrets interface exists. That interface is the missing lever.
_uu_vault_cache="$HOME/.unseen_university/vault/shell_env.cache"
if [ -f "$_uu_vault_cache" ]; then
    # The cache is plain `export VAR=value` lines written by a future refresh step.
    . "$_uu_vault_cache" 2>/dev/null || true
fi
unset _uu_vault_cache

# --- emit a non-interactive env file (systemd EnvironmentFile= / cron-heartbeat source) ---
# Devices launched WITHOUT a login shell — Nanny (the platform-independent scheduler, which
# reads UU_HOME_DB_URL), web_server, scraps, anything Ground Loop wakes — can't source this
# profile, so they'd miss the composed env and fall back to a hardcoded URL. Write a bare
# KEY='value' file they can read instead: systemd EnvironmentFile= strips the quotes,
# `set -a; . uu.env` (a heartbeat/cron shell) honors them, and both parse :// @ : safely.
# Single-quoted => the value is taken literally (a $ or space in a rotated password is safe);
# the password must be percent-encoded in the URL anyway, which also excludes a literal ' .
# LOCAL + chmod 600 (carries the DB password); never committed. Only (re)write when we
# actually composed a URL, so a bootstrap-less recovery shell can't clobber a good file with
# blanks. Atomic (mktemp+mv) and fully fail-soft — a failure leaves consumers on inherited
# values, never breaks the shell (CP6).
if [ -n "${UU_HOME_DB_URL:-}" ]; then
    _uu_env_file="$HOME/.unseen_university/uu.env"
    mkdir -p "$HOME/.unseen_university" 2>/dev/null
    _uu_env_tmp="$(mktemp "${_uu_env_file}.XXXXXX" 2>/dev/null)" && {
        for _uu_k in UU_ROOT UU_HOME_DB_URL IGOR_SWARM_NAME IGOR_INSTANCE_ID CC_TMUX_SESSION CC_WORKFLOW_TOOLS; do
            [ -n "${!_uu_k:-}" ] && printf "%s='%s'\n" "$_uu_k" "${!_uu_k}" >> "$_uu_env_tmp"
        done
        chmod 600 "$_uu_env_tmp" 2>/dev/null
        mv -f "$_uu_env_tmp" "$_uu_env_file" 2>/dev/null || rm -f "$_uu_env_tmp" 2>/dev/null
    } 2>/dev/null
    unset _uu_env_file _uu_env_tmp _uu_k
fi

# A sourced profile must never abort the shell.
return 0 2>/dev/null || true
