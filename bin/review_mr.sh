#!/usr/bin/env bash
# Ask a second model to review a merge request against the evidence behind it, and post
# what it says as a note on the MR.
#
# The reviewer is deliberately not the agent that wrote the change, and deliberately not
# the same model: it is shown the diff and the recorded results, and nothing else, so its
# opinion rests on what a reader of the MR could check.
#
# Usage: ./review_mr.sh <repo-dir> <mr-number> [evidence-file ...]
#
# Env:
#   REVIEW_BASE_URL   proxy speaking the Messages API   (default http://0.0.0.0:4000)
#   REVIEW_MODEL      model name the proxy serves       (default gpt-sol)
#   REVIEW_API_KEY    credential passed upstream        (default $USER)
#   REVIEW_POST       false to print the review instead of posting it
#
# The note names the model that wrote it. The proxy knows which model an alias resolves
# to, so ask it rather than making the reader look up what the alias was that week.
set -euo pipefail

REPO_DIR="${1:?usage: $0 <repo-dir> <mr-number> [evidence-file ...]}"
MR="${2:?usage: $0 <repo-dir> <mr-number> [evidence-file ...]}"
shift 2
BASE_URL="${REVIEW_BASE_URL:-http://0.0.0.0:4000}"
MODEL="${REVIEW_MODEL:-gpt-sol}"
API_KEY="${REVIEW_API_KEY:-$USER}"

# Evidence paths are the caller's, so resolve them before moving into the repo.
EVIDENCE_FILES=()
for f in "$@"; do EVIDENCE_FILES+=( "$(readlink -f "$f")" ); done

cd "$REPO_DIR"
DIFF="$(glab mr diff "$MR")"
[ -n "$DIFF" ] || { echo "review_mr: MR $MR has no diff" >&2; exit 4; }

# Evidence is truncated per file: a reviewer needs enough to check a claim, not a data
# dump, and the whole prompt has to fit whatever context the model has.
EVIDENCE=""
for f in "${EVIDENCE_FILES[@]+"${EVIDENCE_FILES[@]}"}"; do
    [ -f "$f" ] || { echo "review_mr: no such evidence file: $f" >&2; continue; }
    EVIDENCE="$EVIDENCE

--- $(basename "$f") ---
$(head -c 20000 "$f")"
done

export REVIEW_DIFF="$DIFF" REVIEW_EVIDENCE="$EVIDENCE"
REVIEW="$(python3 - "$BASE_URL" "$MODEL" "$API_KEY" <<PYEOF
import json, os, sys, urllib.request

base_url, model, api_key = sys.argv[1:4]
prompt = f"""You are reviewing a merge request to a shared skills repository. A skill is
instructions another agent will follow later, so a claim that is wrong or unsupported
propagates into work you will never see.

You are not the author. Judge only what is here.

Say whether each claim in the change is supported by the evidence below, name anything
asserted that the evidence does not show, and note anything a later reader would need
that is missing. If it is sound, say so plainly and briefly. Do not restate the diff.

Keep it under 200 words, plain text, no markdown headings.

# The change

{os.environ['REVIEW_DIFF']}

# Evidence behind it

{os.environ['REVIEW_EVIDENCE'] or '(none supplied)'}
"""

req = urllib.request.Request(
    base_url.rstrip("/") + "/v1/messages",
    data=json.dumps({"model": model, "max_tokens": 900,
                     "messages": [{"role": "user", "content": prompt}]}).encode(),
    headers={"content-type": "application/json", "x-api-key": api_key,
             "anthropic-version": "2023-06-01"})
with urllib.request.urlopen(req, timeout=300) as r:
    body = json.load(r)
print("".join(b.get("text", "") for b in body.get("content", [])).strip())
PYEOF
)"

[ -n "$REVIEW" ] || { echo "review_mr: the reviewer returned nothing" >&2; exit 1; }

if [ "${REVIEW_POST:-true}" = "false" ]; then
    printf '%s\n' "$REVIEW"
    exit 0
fi
LABEL="$(curl -sS -m 10 "$BASE_URL/model/info" -H "x-api-key: $API_KEY" 2>/dev/null | python3 -c "
import json, sys
try:
    for m in json.load(sys.stdin).get('data', []):
        if m.get('model_name') == sys.argv[1]:
            print(m.get('litellm_params', {}).get('model', '').split('/')[-1]); break
except Exception:
    pass
" "$MODEL" 2>/dev/null)"
NOTE="$(printf '**Automated review by %s**\n\n%s' "${LABEL:-$MODEL}" "$REVIEW")"
glab mr note "$MR" --message "$NOTE" >/dev/null
echo "review_mr: posted to MR $MR"
