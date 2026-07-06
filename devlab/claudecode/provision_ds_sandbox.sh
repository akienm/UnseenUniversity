#!/usr/bin/env bash
# provision_ds_sandbox.sh — ONE-TIME root setup for the DS harvest sandbox.
#
# WHY: the harvest instrument (T-ds-harvest-corpus-batch) runs an edit-capable local model
# (devstral) that HARD-ORIENTS on the live repo via absolute paths (`cd ~/dev/src/UnseenUniversity`,
# `/home/akien/...`). The first batch proved the cwd seam leaks (0 scratch refs, all real-repo). The
# fix Akien chose: a DEDICATED, RESETTABLE checkout owned by a separate user `dicksimnel`, so the
# model orients on ITS OWN throwaway tree (authentic) and the live tree is permission-unreachable
# (safe). This script provisions that once. Review it before running — it creates a user, a checkout,
# a venv, a sudoers drop-in, and (SAFETY-CRITICAL, flagged below) tightens ~/akien home perms.
#
# RUN (once):   sudo bash devlab/claudecode/provision_ds_sandbox.sh
# Idempotent: safe to re-run.
set -euo pipefail

DS_USER="dicksimnel"
DS_HOME="/home/${DS_USER}"
DS_CHECKOUT="${DS_HOME}/dev/src/UnseenUniversity"
SRC_CHECKOUT="/home/akien/dev/src/UnseenUniversity"
AKIEN_HOME="/home/akien"

echo "== 1. create user ${DS_USER} =="
if id "${DS_USER}" >/dev/null 2>&1; then
  echo "   user exists — skip"
else
  useradd -m -s /bin/bash "${DS_USER}"
  echo "   created ${DS_USER} (home ${DS_HOME})"
fi

echo "== 2. clone a throwaway checkout at ${DS_CHECKOUT} =="
if [ -d "${DS_CHECKOUT}/.git" ]; then
  echo "   checkout exists — skip clone"
else
  sudo -u "${DS_USER}" mkdir -p "${DS_HOME}/dev/src"
  # Local clone from akien's checkout (fast; no network). origin is inherited from it.
  sudo -u "${DS_USER}" git clone "${SRC_CHECKOUT}" "${DS_CHECKOUT}"
  echo "   cloned"
fi
chown -R "${DS_USER}:${DS_USER}" "${DS_HOME}/dev"

echo "== 3. venv + editable install as ${DS_USER} =="
if [ -d "${DS_CHECKOUT}/.venv" ]; then
  echo "   venv exists — skip"
else
  sudo -u "${DS_USER}" python3 -m venv "${DS_CHECKOUT}/.venv"
  sudo -u "${DS_USER}" bash -lc "cd '${DS_CHECKOUT}' && . .venv/bin/activate && pip install -q -U pip && pip install -q -e ."
  echo "   installed"
fi

echo "== 4. passwordless sudo: akien -> ${DS_USER} (for non-interactive harvest) =="
SUDOERS="/etc/sudoers.d/harvest-ds"
if [ -f "${SUDOERS}" ]; then
  echo "   sudoers drop-in exists — skip"
else
  echo "akien ALL=(${DS_USER}) NOPASSWD: ALL" > "${SUDOERS}"
  chmod 0440 "${SUDOERS}"
  visudo -cf "${SUDOERS}" >/dev/null && echo "   installed + validated" || { rm -f "${SUDOERS}"; echo "   INVALID sudoers — removed"; exit 1; }
fi

echo "== 5. SAFETY-CRITICAL: make ${AKIEN_HOME} unreachable to ${DS_USER} =="
echo "   (current perms: $(stat -c '%A' "${AKIEN_HOME}"))"
echo "   devstral emits literal /home/akien/... paths; without this, dicksimnel could read/write the"
echo "   live tree. Removing OTHER access (o-rwx) makes those paths permission-denied = safe + a signal."
echo "   REVIEW: if any service relies on world-access to ${AKIEN_HOME}, comment the next line out."
chmod o-rwx "${AKIEN_HOME}"
echo "   new perms: $(stat -c '%A' "${AKIEN_HOME}")"

echo "== 6. verify =="
echo -n "   dicksimnel can import the stack: "
sudo -u "${DS_USER}" bash -lc "cd '${DS_CHECKOUT}' && . .venv/bin/activate && python -c 'import unseen_university; print(\"OK\")'" || echo "FAILED"
echo -n "   dicksimnel CANNOT read akien home: "
sudo -u "${DS_USER}" bash -lc "ls ${SRC_CHECKOUT} >/dev/null 2>&1 && echo 'LEAK — still readable!' || echo 'OK (permission denied)'"
echo -n "   akien can sudo to dicksimnel non-interactively: "
sudo -n -u "${DS_USER}" true 2>/dev/null && echo "OK" || echo "FAILED"

echo ""
echo "DONE. Harvest sandbox ready. Next (as akien): the batch runs the coding domain AS ${DS_USER}"
echo "against ${DS_CHECKOUT}, git-resetting it between seeds. See harvest_batch.py --checkout mode."
