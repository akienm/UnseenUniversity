#!/usr/bin/env bash
# groundloop_start.sh — manual start/stop/status for Ground Loop
#
# Linux (systemd): wraps `systemctl --user <cmd> ground_loop`
# macOS / fallback: manages the Python process directly via PID file
#
# Usage:
#   groundloop_start.sh [start|stop|restart|status|install]
#
# Environment:
#   UU_ROOT     — repo root (auto-detected from script location if unset)
#   IGOR_HOME   — runtime data dir (default: ~/.unseen_university)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UU_ROOT="${UU_ROOT:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
IGOR_HOME="${IGOR_HOME:-$HOME/.unseen_university}"
VENV_PYTHON="$UU_ROOT/.venv/bin/python3"
PID_FILE="$IGOR_HOME/ground_loop.pid"
CMD="${1:-start}"

_has_systemd() {
    command -v systemctl >/dev/null 2>&1 && systemctl --user is-system-running >/dev/null 2>&1
}

_systemd_cmd() {
    systemctl --user "$1" ground_loop
}

_is_running_direct() {
    [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

_start_direct() {
    if _is_running_direct; then
        echo "ground_loop: already running (pid=$(cat "$PID_FILE"))"
        return 0
    fi
    mkdir -p "$IGOR_HOME/ground_loop"
    # Deploy plugin descriptors before starting
    cp "$UU_ROOT/config/ground_loop/"*.yaml "$IGOR_HOME/ground_loop/" 2>/dev/null || true
    echo "ground_loop: starting..."
    nohup "$VENV_PYTHON" -m unseen_university.devices.ground_loop.daemon \
        --poll 15 \
        --log-level INFO \
        >"$IGOR_HOME/ground_loop.log" 2>&1 &
    echo $! >"$PID_FILE"
    echo "ground_loop: started (pid=$(cat "$PID_FILE"))"
}

_stop_direct() {
    if ! _is_running_direct; then
        echo "ground_loop: not running"
        return 0
    fi
    PID=$(cat "$PID_FILE")
    kill "$PID" 2>/dev/null && echo "ground_loop: stopped (pid=$PID)" || echo "ground_loop: kill failed"
    rm -f "$PID_FILE"
}

_status_direct() {
    if _is_running_direct; then
        echo "ground_loop: running (pid=$(cat "$PID_FILE"))"
    else
        echo "ground_loop: not running"
    fi
}

case "$CMD" in
    start)
        if _has_systemd; then _systemd_cmd start
        else _start_direct; fi
        ;;
    stop)
        if _has_systemd; then _systemd_cmd stop
        else _stop_direct; fi
        ;;
    restart)
        if _has_systemd; then _systemd_cmd restart
        else _stop_direct; sleep 1; _start_direct; fi
        ;;
    status)
        if _has_systemd; then _systemd_cmd status
        else _status_direct; fi
        ;;
    install)
        if _has_systemd; then
            UNIT_SRC="$UU_ROOT/systemd/ground_loop.user.service"
            UNIT_DST="$HOME/.config/systemd/user/ground_loop.service"
            mkdir -p "$(dirname "$UNIT_DST")"
            cp "$UNIT_SRC" "$UNIT_DST"
            systemctl --user daemon-reload
            systemctl --user enable --now ground_loop.service
            echo "ground_loop: installed and started via systemd"
        else
            echo "ground_loop: systemd not available — use 'start' to run directly"
            _start_direct
        fi
        ;;
    *)
        echo "Usage: $0 [start|stop|restart|status|install]" >&2
        exit 1
        ;;
esac
