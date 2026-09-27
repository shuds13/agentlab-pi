#!/bin/bash
# Launch this campaign with pi as the harness instead of the Claude Agent SDK.
# Same campaign, same tools, same records -- a different agent runtime.
#
#     ./run_pi.sh            # run it
#     WATCH=true ./run_pi.sh # ...and serve the run viewer
set -euo pipefail

cd "$(dirname "$0")"

. ../../framework/settings.sh
export CAMPAIGN="$(basename "$PWD")"
export USER_NAME="${USER_NAME:-$USER}"

# Same budget as run.sh: jobs take about a second, so a run that proves the machinery
# works is minutes.
export MAX_SUBMITS=24
export MAX_RUNTIME=900

# Which model pi runs as. Left unset, pi uses its configured default.
# export AGENT_MODEL=claude-opus-5

echo "[run] CAMPAIGN=$CAMPAIGN USER=$USER_NAME harness=pi"
python -u ../../framework/run_pi.py "$@"
