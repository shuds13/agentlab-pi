#!/bin/bash
# OPTIONAL. Set up the agent's `transfer` tool: Globus Transfer, so the agent can read
# files on the compute system -- job logs above all -- when it does not share a
# filesystem with it. See docs/globus_transfer.md.
#
# Run it and answer the prompts:
#
#   bin/setup_globus.sh
#
# It is safe to re-run: it re-checks every step and only does what is missing. Nothing
# here is destructive, and it writes exactly one file -- users/<you>/<system>.json.
#
#   --system <name>   which system to configure (default: $SYSTEM, else asked)
#   --check           report what is and is not set up, change nothing
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"   # the globus CLI often lands here

LAB_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SYSTEM="${SYSTEM:-}"
USER_NAME="${USER_NAME:-$USER}"
CHECK_ONLY=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --system) SYSTEM="${2:-}"; shift ;;
    --check)  CHECK_ONLY=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "usage: $0 [--system <name>] [--check]" >&2; exit 2 ;;
  esac
  shift
done

say()  { printf '%s\n' "$*"; }
step() { printf '\n=== %s ===\n' "$*"; }
ok()   { printf '  OK    %s\n' "$*"; }
todo() { printf '  TODO  %s\n' "$*"; }

# ---------------------------------------------------------------- which system
if [ -z "$SYSTEM" ]; then
  mapfile -t systems < <(cd "$LAB_DIR/systems" 2>/dev/null && ls *.json 2>/dev/null | sed 's/\.json$//')
  if [ "${#systems[@]}" -eq 1 ]; then
    SYSTEM="${systems[0]}"
  elif [ "${#systems[@]}" -eq 0 ]; then
    say "No systems defined in $LAB_DIR/systems/." >&2; exit 1
  else
    say "Which system? ${systems[*]}"
    read -r -p "system: " SYSTEM
  fi
fi
USER_FILE="$LAB_DIR/users/$USER_NAME/$SYSTEM.json"
say "System: $SYSTEM     User file: $USER_FILE"

# ---------------------------------------------------------------- 1. globus CLI
step "1. Globus CLI"
if command -v globus >/dev/null 2>&1; then
  ok "globus CLI found: $(command -v globus)"
else
  todo "globus CLI not on PATH"
  [ "$CHECK_ONLY" = 1 ] && exit 1
  say "  Installing into your user site-packages..."
  pip install --user --quiet globus-cli
  command -v globus >/dev/null 2>&1 || { say "  Install failed. Try: pip install globus-cli" >&2; exit 1; }
  ok "installed"
fi

# ---------------------------------------------------------------- 2. logged in
step "2. Globus login"
if WHO=$(globus whoami 2>/dev/null); then
  ok "logged in as $WHO"
else
  todo "not logged in"
  [ "$CHECK_ONLY" = 1 ] && exit 1
  say "  A browser login follows. Paste the code back here when asked."
  globus login
  WHO=$(globus whoami)
  ok "logged in as $WHO"
fi

# ---------------------------------------------------------------- 3. this machine
step "3. Globus Connect Personal (the background service on this machine)"
# Files on this machine are only reachable while this service is running. An HPC login
# node usually has a site collection instead and needs none of this.
GCP_DIR="$(ls -d "$HOME"/globusconnectpersonal-*/ 2>/dev/null | head -1 || true)"

if [ -z "$GCP_DIR" ]; then
  todo "Globus Connect Personal is not installed"
  if [ "$CHECK_ONLY" = 0 ]; then
    say "  It makes this machine into a Globus collection so files here can be"
    say "  transferred. Skip it only if this machine already has a site collection."
    read -r -p "  install Globus Connect Personal? [Y/n] " a
    if [ "${a:-Y}" != "n" ] && [ "${a:-Y}" != "N" ]; then
      ( cd "$HOME" \
        && curl -fsSLO https://downloads.globus.org/globus-connect-personal/linux/stable/globusconnectpersonal-latest.tgz \
        && tar xzf globusconnectpersonal-latest.tgz )
      GCP_DIR="$(ls -d "$HOME"/globusconnectpersonal-*/ | head -1)"
      say "  Setting it up. Open the URL it prints, log in, paste the code back."
      ( cd "$GCP_DIR" && ./globusconnectpersonal -setup --no-gui )
      ok "installed at $GCP_DIR"
    fi
  fi
else
  ok "installed at $GCP_DIR"
fi

if [ -n "$GCP_DIR" ]; then
  if "$GCP_DIR/globusconnectpersonal" -status 2>/dev/null | grep -qi "connected"; then
    ok "running"
  else
    todo "not running -- transfers to and from this machine will fail"
    if [ "$CHECK_ONLY" = 0 ]; then
      read -r -p "  start Globus Connect Personal now? [Y/n] " a
      if [ "${a:-Y}" != "n" ] && [ "${a:-Y}" != "N" ]; then
        # setsid, or it dies with this script and the next step fails confusingly.
        GCP_LOG="$HOME/.globusonline/gcp-start.log"
        ( cd "$GCP_DIR" && setsid nohup ./globusconnectpersonal -start \
            >>"$GCP_LOG" 2>&1 < /dev/null & ) || true
        for _ in $(seq 1 20); do
          sleep 1
          "$GCP_DIR/globusconnectpersonal" -status 2>/dev/null | grep -qi "connected" && break
        done
        if "$GCP_DIR/globusconnectpersonal" -status 2>/dev/null | grep -qi "connected"; then
          ok "started, and it stays up after this script exits"
        else
          todo "it did not come up -- see $GCP_LOG"
        fi
      fi
    fi
  fi
fi

# Globus Connect Personal serves only the paths listed in config-paths, and the default
# "~/" entry does not grant writes -- a transfer into $HOME fails with PERMISSION_DENIED
# on the directory create. The workspace has to be listed explicitly.
if [ -n "$GCP_DIR" ]; then
  CFG_PATHS="$HOME/.globusonline/lta/config-paths"
  WS_ROOT="$LAB_DIR/"
  # A parent entry covers the workspace, so test prefix coverage, not an exact line.
  if COVER=$(CFG_PATHS="$CFG_PATHS" WS_ROOT="$WS_ROOT" python3 -c '
import os, sys
cfg, ws = os.environ["CFG_PATHS"], os.path.realpath(os.environ["WS_ROOT"])
try: lines = open(cfg).read().splitlines()
except OSError: sys.exit(1)
for ln in lines:
    ln = ln.strip()
    if not ln or ln.startswith("#"): continue
    parts = ln.split(",")
    path = os.path.realpath(os.path.expanduser(parts[0].strip()))
    rw = parts[2].strip() if len(parts) > 2 else "1"
    if rw == "1" and (ws == path or ws.startswith(path.rstrip("/") + "/")):
        print(parts[0].strip()); sys.exit(0)
sys.exit(1)' 2>/dev/null); then
    ok "workspace shared by Globus Connect Personal (via $COVER)"
  else
    todo "$WS_ROOT is not in $CFG_PATHS -- transfers into the workspace will be refused"
    if [ "$CHECK_ONLY" = 0 ]; then
      read -r -p "  add it and restart Globus Connect Personal? [Y/n] " a
      if [ "${a:-Y}" != "n" ] && [ "${a:-Y}" != "N" ]; then
        mkdir -p "$(dirname "$CFG_PATHS")"
        [ -f "$CFG_PATHS" ] && cp "$CFG_PATHS" "$CFG_PATHS.bak.$(date +%Y%m%d%H%M%S)"
        printf '%s,0,1\n' "$WS_ROOT" >> "$CFG_PATHS"
        "$GCP_DIR/globusconnectpersonal" -stop >/dev/null 2>&1 || true
        sleep 3
        ( cd "$GCP_DIR" && setsid nohup ./globusconnectpersonal -start \
            >>"$HOME/.globusonline/gcp-start.log" 2>&1 < /dev/null & ) || true
        for _ in $(seq 1 20); do
          sleep 1
          "$GCP_DIR/globusconnectpersonal" -status 2>/dev/null | grep -qi "connected" && break
        done
        ok "added and restarted"
      fi
    fi
  fi
fi

# ---------------------------------------------------------------- 3b. which collection
step "4. Which collection is this machine?"
mapfile -t mine < <(globus endpoint search --filter-scope my-endpoints --format unix \
                      --jmespath 'DATA[].[id,display_name]' 2>/dev/null || true)
if [ "${#mine[@]}" -eq 0 ]; then
  todo "you own no collections"
  [ "$CHECK_ONLY" = 1 ] || { say "  Set up Globus Connect Personal first." >&2; exit 1; }
else
  i=1; for row in "${mine[@]}"; do printf '    %d) %s\n' "$i" "$row"; i=$((i+1)); done
fi

[ "$CHECK_ONLY" = 1 ] || {
  read -r -p "  this machine is number [1]: " LOCAL_ID
  LOCAL_ID="${LOCAL_ID:-1}"
  # A number picks from the list just printed; anything else is taken as a UUID.
  if [[ "$LOCAL_ID" =~ ^[0-9]+$ ]]; then
    pick="${mine[$((LOCAL_ID-1))]:-}"
    [ -n "$pick" ] || { say "  No collection numbered $LOCAL_ID." >&2; exit 1; }
    LOCAL_ID="${pick%%[[:space:]]*}"
  fi
  say "  -> $LOCAL_ID"
}

# ---------------------------------------------------------------- 4. the compute side
# The site's collection is a machine fact, the same for everyone, so it is stated once
# in systems/<system>.json rather than searched for by each user. Searching is a poor
# substitute: sites publish a per-project guest collection for every project on the
# machine, and users publish their own, so a name search returns hundreds of look-alikes.
REMOTE_ID=$(python3 -c "
import json
try: print(json.load(open('$LAB_DIR/systems/$SYSTEM.json')).get('globus_collection',''))
except Exception: print('')" 2>/dev/null)
REMOTE_NAME=$(python3 -c "
import json
try: print(json.load(open('$LAB_DIR/systems/$SYSTEM.json')).get('globus_collection_name',''))
except Exception: print('')" 2>/dev/null)

if [ "$CHECK_ONLY" = 0 ]; then
  step "5. The compute system's collection"
  if [ -n "$REMOTE_ID" ]; then
    ok "${REMOTE_NAME:-$SYSTEM}  $REMOTE_ID"
    say "  (from systems/$SYSTEM.json)"
  else
    todo "systems/$SYSTEM.json has no globus_collection"
    say "  Find the collection for $SYSTEM in the Globus web app (Collections), then"
    say "  add it to systems/$SYSTEM.json so nobody has to look it up again:"
    say ""
    say '      "globus_collection": "<uuid>",'
    say '      "globus_collection_name": "<name>",'
    say ""
    read -r -p "  or paste the UUID now to continue: " REMOTE_ID
    [ -n "$REMOTE_ID" ] || { say "  Nothing to configure against." >&2; exit 1; }
  fi
fi

# ---------------------------------------------------------------- 5. consent
# work_dir is the path that matters and is already recorded, so it is both the consent
# probe and, later, the default writable root. Nothing here lists a directory of
# projects.
WORK_DIR=$(python3 -c "
import json
try: print(json.load(open('$USER_FILE')).get('work_dir',''))
except Exception: print('')" 2>/dev/null)

if [ "$CHECK_ONLY" = 0 ]; then
  step "6. Consent for the compute system's collection"
  # Only Globus Connect Server v5 collections have a data_access scope. Globus Connect
  # Personal collections do not, and asking for one fails with UNKNOWN_SCOPE_ERROR --
  # so this asks for the remote collection alone.
  SCOPE="urn:globus:auth:scope:transfer.api.globus.org:all[*https://auth.globus.org/scopes/$REMOTE_ID/data_access]"
  if globus ls "$REMOTE_ID:${WORK_DIR:-/}" >/dev/null 2>&1; then
    ok "already consented"
  else
    say "  A browser login follows. Paste the code back here when asked."
    globus session consent "$SCOPE" || say "  (consent failed -- see the message above)"
  fi
fi

# ---------------------------------------------------------------- 6. write the config
step "7. Write $USER_FILE"
if [ "$CHECK_ONLY" = 1 ]; then
  if [ -f "$USER_FILE" ] && python3 -c "import json,sys; sys.exit(0 if (json.load(open('$USER_FILE')).get('globus') or {}).get('remote_collection') else 1)" 2>/dev/null; then
    ok "globus block present"
    python3 -c "import json; print(json.dumps(json.load(open('$USER_FILE'))['globus'], indent=2))" | sed 's/^/    /'
  else
    todo "no globus block in $USER_FILE"
  fi
  step "check complete"; exit 0
fi

say "  Three limits bound what the agent can touch. Press enter to accept a default."
say ""
say "  1. WRITE on $SYSTEM  -- where 'put' may write. Everything else is read-only."
read -r -p "     writable root [${WORK_DIR:-none}]: " WRITE_ROOT
WRITE_ROOT="${WRITE_ROOT:-$WORK_DIR}"
say ""
say "  2. READ on $SYSTEM   -- blank means anywhere your account can read, which is"
say "     usually what you want: job logs live under $WORK_DIR but a path you"
say "     want to fetch may not."
read -r -p "     readable root [blank = no limit]: " READ_ROOT
say ""
say "  3. On this machine   -- fixed: the campaign workspace, in and out. Not settable."
say ""
say "  These are guardrails against a wrong path. Your account's own permissions on"
say "  $SYSTEM are what actually stop you reaching anyone else's data."
say "  Change any of it later in $USER_FILE."

mkdir -p "$(dirname "$USER_FILE")"
LOCAL_ID="$LOCAL_ID" REMOTE_ID="$REMOTE_ID" WRITE_ROOT="$WRITE_ROOT" \
READ_ROOT="$READ_ROOT" USER_FILE="$USER_FILE" python3 - <<'PYEOF'
import json, os
path = os.environ["USER_FILE"]
try:
    with open(path) as f:
        cfg = json.load(f)
except FileNotFoundError:
    cfg = {}
cfg["globus"] = {
    "_comment": "Written by bin/setup_globus.sh. See docs/globus_transfer.md.",
    "remote_collection": os.environ["REMOTE_ID"],
    "local_collection": os.environ["LOCAL_ID"],
}
if os.environ.get("WRITE_ROOT"):
    cfg["globus"]["remote_write_root"] = os.environ["WRITE_ROOT"]
if os.environ.get("READ_ROOT"):
    cfg["globus"]["remote_read_root"] = os.environ["READ_ROOT"]
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")
print("  wrote", path)
PYEOF

# ---------------------------------------------------------------- 7. prove it works
step "8. Test"
if globus ls "$REMOTE_ID:${WRITE_ROOT:-/}" >/dev/null 2>&1; then
  ok "listed ${WRITE_ROOT:-/} on the remote collection"
  say ""
  say "Done. The agent now has a 'transfer' tool (ls / get / put)."
  say "Re-check any time with: bin/setup_globus.sh --system $SYSTEM --check"
else
  todo "could not list ${WRITE_ROOT:-/} on the remote collection"
  say "  The config is written. Run this to see the actual error:"
  say "    globus ls $REMOTE_ID:${WRITE_ROOT:-/}"
  exit 1
fi
