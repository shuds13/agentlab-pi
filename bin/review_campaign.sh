#!/usr/bin/env bash
# Read a campaign before it runs and report what would waste a machine or a budget.
#
# Everything a campaign does is decided by a handful of small files, so this is one
# request rather than an agent: it is handed those files and answers from them. Run it
# when the campaign is filled in and before the first job, while changing task.py is
# still free.
#
# Usage: ./review_campaign.sh <campaign> [--model NAME]
#
# The review is written to campaigns/<campaign>/EFFICIENCY-REVIEW.md as well as printed,
# so what was raised before the first job can be checked against what was changed.
#
# The reviewing model is the critic's if one is configured (CRITIC_MODEL and its
# gateway, see docs/settings.md), otherwise the agent's own. Neither is required: with
# no gateway it goes to whatever ANTHROPIC_BASE_URL serves.
set -euo pipefail
cd "$(dirname "$0")"
LAB_DIR="$(cd .. && pwd)"

CAMPAIGN="${1:?usage: $0 <campaign> [--model NAME]}"
shift || true
MODEL=""
[ "${1:-}" = "--model" ] && MODEL="${2:?--model needs a name}"

DIR="$LAB_DIR/campaigns/$CAMPAIGN"
[ -d "$DIR" ] || { echo "no campaign at $DIR" >&2; exit 2; }

# Everything that decides what the campaign does, and nothing else: the review is only
# as good as its evidence, and a reviewer given the workspace would review the results.
FILES=(campaign.json task.py prompt.md user_prompt.md method.md run.sh)
EVIDENCE=""
for f in "${FILES[@]}"; do
    [ -f "$DIR/$f" ] || continue
    EVIDENCE="$EVIDENCE

--- $f ---
$(head -c 40000 "$DIR/$f")"
done
[ -n "$EVIDENCE" ] || { echo "nothing to review in $DIR" >&2; exit 2; }

# The machine it will run on bounds what is sensible: a concurrency of one means
# something different on a laptop than on a queue that would take eight.
SYSTEM="$(python3 -c "
import json, sys
try:
    name = json.load(open(sys.argv[1]))['system']
except Exception:
    sys.exit(0)
print(name)
" "$DIR/campaign.json" 2>/dev/null || true)"
if [ -n "$SYSTEM" ] && [ -f "$LAB_DIR/systems/$SYSTEM.json" ]; then
    EVIDENCE="$EVIDENCE

--- systems/$SYSTEM.json ---
$(cat "$LAB_DIR/systems/$SYSTEM.json")"
fi

export REVIEW_EVIDENCE="$EVIDENCE" REVIEW_CAMPAIGN="$CAMPAIGN" REVIEW_MODEL="$MODEL"
export REVIEW_DIR="$DIR" REVIEW_FRAMEWORK="$LAB_DIR/framework"
python3 - <<'PYEOF'
import os
import sys

sys.path.insert(0, os.environ["REVIEW_FRAMEWORK"])
import critic

model = os.environ.get("REVIEW_MODEL") or ""
if not model:
    try:
        model, label = critic.resolve()
    except critic.CriticUnavailable:
        model, label = None, None
    # No critic configured is not a reason to skip the review: it goes wherever the
    # agent's own model is served.
    model = model or os.environ.get("ANTHROPIC_MODEL") or "claude-opus-5"

with open(os.path.join(os.environ["REVIEW_FRAMEWORK"],
                       "review_campaign_prompt.md")) as f:
    prompt = f.read()

reply = critic.review(
    model,
    f"# The campaign: {os.environ['REVIEW_CAMPAIGN']}\n" + os.environ["REVIEW_EVIDENCE"],
    "",
    prompt=prompt,
)
print(reply or "the reviewer returned nothing", flush=True)

# Kept beside the campaign it judged, and appended: a second review after the fixes
# should sit under the first, so the pair shows what was raised and what was done.
if reply:
    from datetime import datetime

    out = os.path.join(os.environ["REVIEW_DIR"], "EFFICIENCY-REVIEW.md")
    try:
        with open(out, "a") as f:
            f.write(f"\n## {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} -- {model}, "
                    f"before the run\n\n{reply}\n")
        print(f"\nwritten to {out}", flush=True)
    except OSError as e:
        print(f"could not write the review (ignored): {e}", flush=True)
PYEOF
