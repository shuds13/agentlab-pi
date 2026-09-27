#!/usr/bin/env bash
# Start one run of an existing campaign. This is the only way a run begins other than
# a person running the campaign's own run.sh, and it is what the secretary is allowed
# to call, so it checks rather than trusts:
#
#   - the campaign is named in SLACK_CAMPAIGNS, so reachable from Slack only when
#     someone deliberately put it there
#   - the campaign exists and has a run.sh
#   - no agent is already live on it
#
# Usage: ./start_run.sh <campaign> [reason]
set -euo pipefail
cd "$(dirname "$0")"
LAB_DIR="$(cd .. && pwd)"
ALIVE_WITHIN="${AGENT_ALIVE_WITHIN:-300}"   # s; a heartbeat fresher than this = running

CAMPAIGN="${1:-}"
REASON="${2:-started via start_run.sh}"
[ -n "$CAMPAIGN" ] || { echo "usage: $0 <campaign> [reason]" >&2; exit 2; }
case "$CAMPAIGN" in
  *[!A-Za-z0-9._-]*|.*) echo "start_run: bad campaign name '$CAMPAIGN'" >&2; exit 2 ;;
esac

# Allowlist. Space- or comma-separated; empty means nothing may be started this way.
ALLOWED="${SLACK_CAMPAIGNS:-}"
ALLOWED="${ALLOWED//,/ }"
ok=false
for c in $ALLOWED; do [ "$c" = "$CAMPAIGN" ] && ok=true; done
$ok || { echo "start_run: '$CAMPAIGN' is not in SLACK_CAMPAIGNS" >&2; exit 3; }

RUN_SH="$LAB_DIR/campaigns/$CAMPAIGN/run.sh"
[ -x "$RUN_SH" ] || { echo "start_run: no runnable $RUN_SH" >&2; exit 4; }

# Refuse a second agent on a campaign that already has one. Concurrent agents are a
# deliberate choice a person makes by running run.sh again, not a thing to do by accident.
now=$(date +%s)
for hb in "$LAB_DIR/workspace/$CAMPAIGN"/runs/*/heartbeat; do
  [ -f "$hb" ] || continue
  beat=$(cut -d. -f1 < "$hb" 2>/dev/null || echo 0)
  # A run that has recorded its own end does not hold the campaign, even while its
  # last heartbeat is still recent.
  status=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('status',''))" \
             "$(dirname "$hb")/meta.json" 2>/dev/null || echo "")
  if [ "$status" != "running" ]; then continue; fi
  if [ $((now - beat)) -le "$ALIVE_WITHIN" ]; then
    echo "start_run: '$CAMPAIGN' already has a live agent ($(basename "$(dirname "$hb")"))" >&2
    exit 5
  fi
done

export STARTED_BY="$REASON"        # recorded in the run's meta.json
# Cooldown. Two requests that read the same way, a confirmation repeated, a message
# delivered twice -- none of them should start a second run moments after the first.
COOLDOWN="${START_COOLDOWN:-300}"   # s
LAST="$LAB_DIR/workspace/$CAMPAIGN/run/last_start"
if [ -f "$LAST" ]; then
  since=$(( now - $(cut -d. -f1 < "$LAST" 2>/dev/null || echo 0) ))
  if [ "$since" -lt "$COOLDOWN" ]; then
    echo "start_run: '$CAMPAIGN' was started ${since}s ago; cooldown is ${COOLDOWN}s" >&2
    exit 6
  fi
fi
mkdir -p "$(dirname "$LAST")" && echo "$now" > "$LAST"

# The agent writes its own log, so this launch keeps none of its own.
# Started the way a person would start it: a fresh login shell, so the run gets the
# same environment as running ./run.sh at a terminal. Whoever calls this has an
# environment of their own -- the secretary carries its Slack identity in one -- and
# none of it should reach the run.
if command -v setsid >/dev/null 2>&1; then
  setsid nohup env -i HOME="$HOME" USER="$USER" LOGNAME="${LOGNAME:-$USER}" TERM=dumb \
      bash -lc "cd '$LAB_DIR/campaigns/$CAMPAIGN' && exec ./run.sh" \
      >/dev/null 2>&1 &
else
  # macOS has no setsid command. Fork first because the Python process may already
  # be a process-group leader, then create the isolated group in the child.
  python3 -c 'import os, sys; pid = os.fork(); os._exit(0) if pid else (os.setsid(), os.execvp(sys.argv[1], sys.argv[1:]))' \
      nohup env -i HOME="$HOME" USER="$USER" LOGNAME="${LOGNAME:-$USER}" TERM=dumb \
      bash -lc "cd '$LAB_DIR/campaigns/$CAMPAIGN' && exec ./run.sh" \
      >/dev/null 2>&1 &
fi
echo "start_run: $CAMPAIGN starting -- $REASON"

# The handle is what a person says to address this agent, so wait for the agent to
# publish it rather than reporting a run nobody can name yet.
for _ in $(seq 1 30); do
  sleep 2
  meta=$(ls -t "$LAB_DIR/workspace/$CAMPAIGN"/runs/*/meta.json 2>/dev/null | head -1) || true
  [ -n "${meta:-}" ] || continue
  handle=$(python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(d.get('handle','') if d.get('status')=='running' else '')" "$meta" 2>/dev/null || true)
  if [ -n "$handle" ]; then
    echo "start_run: running as $handle ($(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['run_id'])" "$meta"))"
    exit 0
  fi
done
echo "start_run: started, but no handle published yet -- check $LAB_DIR/workspace/$CAMPAIGN/logs" >&2
exit 0
