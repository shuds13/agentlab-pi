#!/usr/bin/env bash
# Stop one running agent, by handle or run_id. The mirror of start_run.sh, and the only
# stopping the secretary may do, so it checks rather than trusts:
#
#   - the agent is running now, and is found by the name a person actually used
#   - its campaign is named in SLACK_CAMPAIGNS
#   - the stop is a drain, never a signal: the agent finishes its jobs in flight and
#     writes up the cycle. An abrupt stop stays a person's decision (kill_agent.sh --now).
#
# Usage: ./stop_run.sh <handle|run_id> [reason]
set -euo pipefail
cd "$(dirname "$0")"
LAB_DIR="$(cd .. && pwd)"

WANTED="${1:-}"
REASON="${2:-stopped via stop_run.sh}"
[ -n "$WANTED" ] || { echo "usage: $0 <handle|run_id> [reason]" >&2; exit 2; }
case "$WANTED" in
  *[!A-Za-z0-9._-]*|.*) echo "stop_run: bad name '$WANTED'" >&2; exit 2 ;;
esac

# Resolve the name against agents that are running now, so a handle from an old run
# cannot stop whoever holds that handle today.
read -r CAMPAIGN RUN_ID <<<"$(python3 - "$LAB_DIR" "$WANTED" <<'PY'
import glob, json, os, sys
lab, wanted = sys.argv[1], sys.argv[2]
for meta_path in glob.glob(os.path.join(lab, "workspace", "*", "runs", "*", "meta.json")):
    try:
        meta = json.load(open(meta_path))
    except Exception:
        continue
    if meta.get("status") != "running":
        continue
    if wanted in (meta.get("handle"), meta.get("run_id")):
        campaign = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(meta_path))))
        print(campaign, meta["run_id"])
        break
PY
)"
[ -n "${RUN_ID:-}" ] || { echo "stop_run: no running agent called '$WANTED'" >&2; exit 4; }

ALLOWED="${SLACK_CAMPAIGNS:-}"
ALLOWED="${ALLOWED//,/ }"
ok=false
for c in $ALLOWED; do [ "$c" = "$CAMPAIGN" ] && ok=true; done
$ok || { echo "stop_run: '$CAMPAIGN' is not in SLACK_CAMPAIGNS" >&2; exit 3; }

echo "stop_run: draining $WANTED ($RUN_ID, campaign $CAMPAIGN) -- $REASON"
./kill_agent.sh --drain "$RUN_ID"
