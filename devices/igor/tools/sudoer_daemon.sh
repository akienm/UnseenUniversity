#!/usr/bin/env bash
#
# sudoer_daemon.sh — Igor sudo relay daemon (D123)
#
# Purpose:
#   Asks for sudo password once, keeps it alive, watches for Igor-written
#   pending.sh files and executes them with sudo. Human consent = the explicit
#   act of running this daemon. Exits cleanly on Ctrl-C, revoking sudo.
#
# Usage:
#   bash sudoer_daemon.sh          — run the daemon
#   bash sudoer_daemon.sh --test   — smoke-test the relay protocol and exit
#
# Protocol (Igor side):
#   Write:  ~/.unseen_university/sudo_relay/pending.sh   (the commands to run as sudo)
#   Poll:   ~/.unseen_university/sudo_relay/done          (appears when complete)
#   Read:   first line of done = exit code
#   Clean:  rm done when finished reading
#
# Notes:
#   - Only one pending.sh processed at a time (atomic: cp+rm then execute)
#   - Log written to ~/.unseen_university/sudo_relay/daemon.log (newest entries appended)
#   - sudo -k is called on exit to drop privilege immediately

set -euo pipefail

RELAY_DIR="${IGOR_RELAY_DIR:-${HOME}/.unseen_university/sudo_relay}"
mkdir -p "${RELAY_DIR}"

logtarget="${RELAY_DIR}/daemon.log"

################################################################################
# Logging helpers (inlined from akientools/bin/logger_for_bash)
################################################################################
timestamp() {
    date +"%Y-%m-%d.%H:%M:%S.%4N"
}

logecho() {
    echo "$(timestamp) $*" | tee -a "${logtarget}"
}

logcmd() {
    echo "$(timestamp) \$ $*" | tee -a "${logtarget}"
    "$@" 2>&1 | tee -a "${logtarget}"
    result_code=${PIPESTATUS[0]}
    echo "$(timestamp) result_code=${result_code}" | tee -a "${logtarget}"
    echo "" | tee -a "${logtarget}"
    return "${result_code}"
}

################################################################################
# Self-test (--test flag): smoke-test the relay protocol without the main loop
################################################################################
run_self_test() {
    local pass=0 fail=0

    logecho "=== sudoer_daemon self-test ==="

    # Test 1: sudo -v works
    logecho "TEST 1: sudo -v"
    if sudo -v 2>&1 | tee -a "${logtarget}"; then
        logecho "PASS: sudo auth ok"
        (( pass++ )) || true
    else
        logecho "FAIL: sudo auth failed"
        (( fail++ )) || true
    fi
    echo "" | tee -a "${logtarget}"

    # Test 2: execute a benign pending.sh via the relay protocol
    logecho "TEST 2: relay protocol (write pending.sh → execute → read done)"
    rm -f "${RELAY_DIR}/pending.sh" "${RELAY_DIR}/done" "${RELAY_DIR}/executing.sh"
    cat > "${RELAY_DIR}/pending.sh" <<'EOF'
#!/usr/bin/env bash
echo "sudo_relay: test payload executed ok"
whoami
exit 0
EOF
    mv "${RELAY_DIR}/pending.sh" "${RELAY_DIR}/executing.sh"
    sudo bash "${RELAY_DIR}/executing.sh" 2>&1 | tee -a "${logtarget}"
    exec_result=${PIPESTATUS[0]}
    echo "${exec_result}" > "${RELAY_DIR}/done"
    rm -f "${RELAY_DIR}/executing.sh"
    done_val=$(cat "${RELAY_DIR}/done")
    rm -f "${RELAY_DIR}/done"
    if [ "${done_val}" == "0" ]; then
        logecho "PASS: relay protocol ok (exit_code=${done_val})"
        (( pass++ )) || true
    else
        logecho "FAIL: relay protocol returned exit_code=${done_val}"
        (( fail++ )) || true
    fi
    echo "" | tee -a "${logtarget}"

    # Test 3: relay captures non-zero exit code correctly
    logecho "TEST 3: non-zero exit code propagation"
    cat > "${RELAY_DIR}/pending.sh" <<'EOF'
#!/usr/bin/env bash
echo "sudo_relay: intentional failure"
exit 42
EOF
    mv "${RELAY_DIR}/pending.sh" "${RELAY_DIR}/executing.sh"
    set +e
    sudo bash "${RELAY_DIR}/executing.sh" 2>&1 | tee -a "${logtarget}"
    exec_result=${PIPESTATUS[0]}
    set -e
    echo "${exec_result}" > "${RELAY_DIR}/done"
    rm -f "${RELAY_DIR}/executing.sh"
    done_val=$(cat "${RELAY_DIR}/done")
    rm -f "${RELAY_DIR}/done"
    if [ "${done_val}" == "42" ]; then
        logecho "PASS: exit code 42 propagated correctly"
        (( pass++ )) || true
    else
        logecho "FAIL: expected 42, got ${done_val}"
        (( fail++ )) || true
    fi
    echo "" | tee -a "${logtarget}"

    logecho "=== Results: ${pass} passed, ${fail} failed ==="
    logecho "Log: ${logtarget}"
    sudo -k
    [ "${fail}" -eq 0 ]
}

if [ "${1:-}" == "--test" ]; then
    if ! sudo -v; then
        logecho "ERROR: sudo auth failed — cannot run tests"
        exit 1
    fi
    run_self_test
    exit $?
fi

################################################################################
# Cleanup on exit
################################################################################
cleanup() {
    logecho "Sudoer daemon exiting — revoking sudo"
    sudo -k
    logecho "Done."
}
trap cleanup EXIT INT TERM

################################################################################
# Acquire sudo (one password prompt)
################################################################################
logecho "=== Sudoer daemon starting ==="
logecho "Relay dir: ${RELAY_DIR}"
logecho "Log: ${logtarget}"
echo ""

if ! sudo -v; then
    logecho "ERROR: sudo auth failed — exiting"
    exit 1
fi
logecho "Sudo active. Daemon running. Press Ctrl-C to exit and revoke."
echo ""

################################################################################
# Main loop
################################################################################
KEEPALIVE_INTERVAL=24   # iterations × 5s sleep = 120s = 2 min
iteration=0

while true; do
    sleep 5

    # Keepalive: refresh sudo every ~2 minutes
    iteration=$(( iteration + 1 ))
    if (( iteration % KEEPALIVE_INTERVAL == 0 )); then
        sudo -v 2>/dev/null || logecho "WARNING: sudo -v failed (may have expired)"
    fi

    # Check for pending work
    if [ -f "${RELAY_DIR}/pending.sh" ]; then
        # Atomic handoff: move to executing before we touch it
        mv "${RELAY_DIR}/pending.sh" "${RELAY_DIR}/executing.sh"

        logecho "--- Executing pending.sh ---"
        set +e
        sudo bash "${RELAY_DIR}/executing.sh" 2>&1 | tee -a "${logtarget}"
        exec_result=${PIPESTATUS[0]}
        set -e
        logecho "--- Done. exit_code=${exec_result} ---"
        echo ""

        # Signal Igor: write exit code to done file
        echo "${exec_result}" > "${RELAY_DIR}/done"

        # Clean up executing script
        rm -f "${RELAY_DIR}/executing.sh"
    fi
done
