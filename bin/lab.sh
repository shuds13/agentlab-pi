#!/usr/bin/env bash
# The lab's long-running processes, started and stopped in one place.
#
#   ./lab.sh start     start everything in the list below that is not already up
#   ./lab.sh stop      stop what this script started
#   ./lab.sh status    what is up, and where its log is
#
# They belong to the lab, not to a campaign: one of each serves everyone. A campaign
# run is not here -- that is `campaigns/<name>/run.sh`, and it comes and goes.
#
# Which of them run is lab.yaml's business, not this script's: it is the file someone
# reads to see what this lab does.
set -uo pipefail
cd "$(dirname "$0")"
LAB_DIR="$(cd .. && pwd)"
RUN_DIR="$LAB_DIR/workspace/run"
LOG_DIR="$LAB_DIR/workspace/logs"
mkdir -p "$RUN_DIR" "$LOG_DIR"
. ../framework/settings.sh

# How each service starts. A service that needs no starting -- an endpoint someone
# else runs, a proxy already up -- is skipped by `up`, below.
start_background() {
    if command -v setsid >/dev/null 2>&1; then
        setsid nohup "$@" &
    else
        # macOS has no setsid command; os.setpgrp() preserves group shutdown.
        python3 -c 'import os, sys; os.setpgrp(); os.execvp(sys.argv[1], sys.argv[1:])' \
            nohup "$@" &
    fi
}

start_litellm() {
    [ -n "${CRITIC_GATEWAY_START:-}" ] || { echo "  litellm: no litellm-bin in lab.yaml"; return 1; }
    start_background sh -c "$CRITIC_GATEWAY_START" >"$LOG_DIR/litellm.log" 2>&1
    echo $! > "$RUN_DIR/litellm.pid"
}
start_bridge()    { start_background ./run_slack_bridge.sh >"$LOG_DIR/bridge.log" 2>&1; echo $! > "$RUN_DIR/bridge.pid"; }
start_secretary() { start_background ./run_secretary.sh    >"$LOG_DIR/secretary.log" 2>&1; echo $! > "$RUN_DIR/secretary.pid"; }
start_engineer()  { start_background ./run_engineer.sh     >"$LOG_DIR/engineer.log" 2>&1; echo $! > "$RUN_DIR/engineer.pid"; }

# Whether one is already up. The pid file is this script's own record; the proxy is
# checked by asking it, since it may have been started by hand or by a run's preflight.
up() {
    case "$1" in
        litellm)
            curl -sS -m 3 "${CRITIC_BASE_URL:-http://0.0.0.0:4000}/health/liveliness" \
                >/dev/null 2>&1 && return 0 ;;
    esac
    local pid
    pid="$(cat "$RUN_DIR/$1.pid" 2>/dev/null)" || return 1
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

wanted() {
    python3 ../framework/lab_config.py --services
}

# The same list, last first: services are stopped in the opposite order to started.
# Done in the shell because `wanted` is one line of names rather than one per line,
# and because macOS has no `tac`.
wanted_reversed() {
    local out="" s
    for s in $(wanted); do out="$s${out:+ }$out"; done
    printf '%s\n' "$out"
}

# Nothing on is a legitimate answer, but a silent one reads like a broken script.
if [ -z "$(wanted)" ]; then
    if [ -f "$LAB_DIR/lab.yaml" ]; then
        echo "  nothing switched on in lab.yaml"
    else
        echo "  no lab.yaml -- copy lab.yaml.template and switch on what this lab runs"
    fi
    exit 0
fi

case "${1:-status}" in
start)
    for s in $(wanted); do
        if up "$s"; then
            echo "  $s: already running"
            continue
        fi
        if "start_$s"; then
            sleep 2
            up "$s" && echo "  $s: started, log $LOG_DIR/$s.log" \
                     || echo "  $s: did not come up -- see $LOG_DIR/$s.log"
        fi
    done
    ;;
stop)
    # Only what this script recorded starting: a pkill would take someone else's
    # python with it, and these are not the only python on the machine.
    for s in $(wanted_reversed); do
        pid="$(cat "$RUN_DIR/$s.pid" 2>/dev/null)" || { echo "  $s: not started from here"; continue; }
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            # The launchers run their work in children, so stop the group.
            kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null
            echo "  $s: stopped"
        else
            echo "  $s: not running"
        fi
        rm -f "$RUN_DIR/$s.pid"
    done
    ;;
status)
    for s in $(wanted); do
        if up "$s"; then
            echo "  $s: running    $LOG_DIR/$s.log"
        else
            echo "  $s: stopped"
        fi
    done
    ;;
*)
    echo "usage: $0 start|stop|status" >&2
    exit 2
    ;;
esac
