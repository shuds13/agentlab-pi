#!/usr/bin/env python3
"""A campaign run with pi as the harness.

Same shape as agent.py -- one standing conversation, a turn per cycle, jobs submitted
through the framework's tools -- but the conversation is held by pi and the tools live
in their own process (mcp_server.py), reached over MCP by pi and over the control
socket by this runner.

It writes the same run directory as agent.py (meta.json, heartbeat, phase, the prompt
snapshot, the log), so bin/watch.sh shows a pi run exactly as it shows any other.

Usage:
    CAMPAIGN=example-local-compression python3 framework/run_pi.py

Not here yet, on purpose: critic review, Slack notification, the announcements board,
provenance. This is the straight line through a run.
"""

import glob
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

LAB_DIR = os.path.abspath(os.environ.get("LAB_DIR", os.path.join(SCRIPT_DIR, "..")))
CAMPAIGN = os.environ.get("CAMPAIGN") or sys.exit("CAMPAIGN is not set.")
CAMPAIGN_DIR = os.path.join(LAB_DIR, "campaigns", CAMPAIGN)
WORKSPACE_DIR = os.environ.get("WORKSPACE_DIR",
                               os.path.join(LAB_DIR, "workspace", CAMPAIGN))
os.environ["WORKSPACE_DIR"] = WORKSPACE_DIR

import pi_harness  # noqa: E402
import tools_client  # noqa: E402

USER_PROMPT_FILE = os.environ.get("USER_PROMPT_FILE", "user_prompt.md")
AGENT_MODEL = os.environ.get("AGENT_MODEL", "").strip()
MAX_TURNS = int(os.environ.get("MAX_TURNS", "500"))
MAX_EMPTY_TURNS = int(os.environ.get("MAX_EMPTY_TURNS", "3"))
MAX_FINALIZE_TURNS = 2
MAX_RUNTIME = int(os.environ["MAX_RUNTIME"]) if os.environ.get("MAX_RUNTIME") else None
WAIT_POLL = int(os.environ.get("WAIT_POLL", "5"))
HEARTBEAT_INTERVAL = 30
WATCH = os.environ.get("WATCH", "").lower() in ("1", "true", "yes")
WATCH_PORT = int(os.environ.get("WATCH_PORT", "8750"))

RUN_STAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
SYSTEM = json.load(open(os.path.join(CAMPAIGN_DIR, "campaign.json"))).get("system", "local")
SYSTEM = os.environ.get("SYSTEM") or SYSTEM
RUN_ID = f"{SYSTEM}_{RUN_STAMP}"
RUN_DIR = os.path.join(WORKSPACE_DIR, "runs", RUN_ID)
LOG_DIR = os.path.join(WORKSPACE_DIR, "logs")

CONTINUE_PROMPT = ("Results have come in. Read them, record what they show, and decide "
                   "what to try next. Submit the next work.")
EXPLORE_PROMPT = ("Nothing is running and nothing new was proposed. Choose a region of "
                  "the space you have not tried and submit work for it.")
WINDDOWN_PROMPT = ("This run is winding down: submit NO new work. Collect what is still "
                   "running, record the results, and write up what was learned.")
FINALIZE_PROMPT = ("Everything has been collected. Write the final entries in LOGBOOK.md "
                   "and JOURNAL.md now: what was tried, what was found, what to do next.")


# --- the run directory: the contract watch.py reads -------------------------------

_phase_text = None
_last_heartbeat = 0.0


def log(*args):
    print(*args, flush=True)


def set_phase(text):
    """What the run is doing right now. An unchanged phase is left alone so its
    timestamp stays the age of the phase, not the age of the last write."""
    global _phase_text
    if text == _phase_text:
        return
    _phase_text = text
    try:
        tmp = os.path.join(RUN_DIR, "phase.tmp")
        with open(tmp, "w") as f:
            f.write(f"{time.time():.0f}\n{text}\n")
        os.replace(tmp, os.path.join(RUN_DIR, "phase"))
    except OSError:
        pass


def heartbeat(force=True):
    """Liveness is a RECENT heartbeat, so it goes stale by itself if this process dies
    for any reason -- unlike a marker file, which lingers after a crash."""
    global _last_heartbeat
    now = time.time()
    if not force and now - _last_heartbeat < HEARTBEAT_INTERVAL:
        return
    _last_heartbeat = now
    try:
        with open(os.path.join(RUN_DIR, "heartbeat"), "w") as f:
            f.write(str(int(now)))
    except OSError:
        pass


def write_meta(**updates):
    """Merge fields into meta.json, atomically: the viewer reads this while the run
    writes it, and a half-written file reads as a missing run."""
    path = os.path.join(RUN_DIR, "meta.json")
    try:
        with open(path) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        meta = {}
    meta.update(updates)
    try:
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(meta, f, indent=2)
        os.replace(tmp, path)
    except OSError as e:
        log(f"[run] meta.json write failed (ignored): {e}")


def start_run_dir():
    """Snapshot the campaign files this run was given, so it stays reproducible after
    the prompts change."""
    os.makedirs(RUN_DIR, exist_ok=True)
    snap = os.path.join(RUN_DIR, "prompts")
    os.makedirs(snap, exist_ok=True)
    for path in sorted(glob.glob(os.path.join(CAMPAIGN_DIR, "*.md"))) + \
            [os.path.join(CAMPAIGN_DIR, "campaign.json")]:
        if os.path.isfile(path) and os.path.getsize(path) <= 256 * 1024:
            shutil.copy2(path, snap)


def start_watcher():
    if not WATCH:
        return None
    os.makedirs(RUN_DIR, exist_ok=True)
    watch_log = open(os.path.join(RUN_DIR, "watch.log"), "w")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(SCRIPT_DIR, "watch.py"), CAMPAIGN,
         "--port", str(WATCH_PORT), "--no-open"],
        stdout=watch_log, stderr=subprocess.STDOUT)
    log(f"watch: http://127.0.0.1:{WATCH_PORT}/")
    return proc


# --- prompts ----------------------------------------------------------------------

def method_path():
    campaign_copy = os.path.join(CAMPAIGN_DIR, "method.md")
    return (campaign_copy if os.path.isfile(campaign_copy)
            else os.path.join(LAB_DIR, "methods", "standard.md"))


def build_system_prompt(tc):
    with open(os.path.join(CAMPAIGN_DIR, "prompt.md")) as f:
        text = f.read()
    with open(os.path.join(SCRIPT_DIR, "framework_prompt.md")) as f:
        text += "\n\n" + f.read()
    with open(method_path()) as f:
        method = f.read()
    at_once = tc.LOCAL_MAX_CONCURRENT if tc.HAS_LOCAL else tc.MAX_CONCURRENT
    text += "\n\n" + method.replace("{jobs_at_once}", str(at_once))

    limits = []
    if tc.HAS_REMOTE:
        limits.append(f"jobs running at once: {tc.MAX_CONCURRENT}")
    if tc.HAS_LOCAL:
        limits.append(f"local jobs running at once: {tc.LOCAL_MAX_CONCURRENT}")
    if MAX_RUNTIME:
        limits.append(f"wall clock for this run: {MAX_RUNTIME}s")
    text += "\n\n# This run\n" + "\n".join(limits)
    # The tools are namespaced by the MCP server they come from, and the agent must
    # call them by the name it is actually offered.
    text += ("\n\n# Your job tools\n"
             "The framework's tools are offered as: "
             + ", ".join(pi_harness.mcp_tool_name("cas", n) for n in tc.TOOL_NAMES) + ".")
    text += (f"\n\n# This agent\nSYSTEM={SYSTEM}.\nEverything this run reads and writes "
             f"lives in {WORKSPACE_DIR}: results.jsonl, LOGBOOK.md, JOURNAL.md and "
             f"claims.jsonl at the top of it, figures/ and scratch/ beneath. "
             f"Use full paths (e.g. {WORKSPACE_DIR}/results.jsonl).")
    return text


def load_user_prompt():
    with open(os.path.join(CAMPAIGN_DIR, USER_PROMPT_FILE)) as f:
        return f.read()


# --- the run ------------------------------------------------------------------------

PHASES = {"read": "reading a file", "write": "writing a file", "edit": "editing a file",
          "bash": "running a shell command", "grep": "searching", "find": "searching"}


def record_session(harness, model_name):
    """Context and cost, for the status pane.

    agent.py had to ask the CLI a throwaway question and interrupt it to learn the
    context window. Pi reports it, so this is a plain read."""
    try:
        stats = harness.session_stats() or {}
    except Exception:
        return
    usage = stats.get("contextUsage") or {}
    fields = {"session_id": stats.get("sessionId"),
              "session_cwd": stats.get("sessionFile")}
    if usage.get("tokens") is not None:
        fields.update(context_tokens=usage["tokens"],
                      context_window=usage.get("contextWindow"),
                      context_pct=usage.get("percent"))
    if stats.get("cost") is not None:
        # The pane shows a cost per model; one harness session is one model.
        fields["cost_models"] = [{"model": model_name, "cost": stats["cost"],
                                  "tokens": (stats.get("tokens") or {}).get("total")}]
    write_meta(**fields)


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(os.path.join(WORKSPACE_DIR, "run"), exist_ok=True)
    start_run_dir()
    watcher = start_watcher()

    # pi starts the tool server (it owns the MCP stdio pipes), so the config naming it
    # is written first, with this run's campaign baked in.
    mcp_config = pi_harness.write_mcp_config(os.path.join(RUN_DIR, "mcp.json"), {
        "cas": {
            "command": sys.executable,
            "args": [os.path.join(SCRIPT_DIR, "mcp_server.py")],
            "env": {"LAB_DIR": LAB_DIR, "CAMPAIGN": CAMPAIGN,
                    "WORKSPACE_DIR": WORKSPACE_DIR, "SYSTEM": SYSTEM,
                    "TOOLS_CONTROL_SOCKET": tools_client.default_socket_path(WORKSPACE_DIR)},
        }
    })

    pid_file = os.path.join(WORKSPACE_DIR, "run", f"agent_{SYSTEM}.pid")
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    stop_reason = "ended (loop finished)"
    start_time = time.time()
    stop_flag = {"stop": False}

    def on_signal(signum, _frame):
        name = signal.Signals(signum).name
        log(f"{name} received -- winding down.")
        stop_flag["stop"] = True
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, on_signal)

    beating = threading.Event()

    def beat():
        while not beating.wait(HEARTBEAT_INTERVAL):
            heartbeat()
    threading.Thread(target=beat, daemon=True).start()

    harness = None
    tc = None
    turn_num = 0
    # Kept as the run goes. The tool server exits with pi, so asking it for the totals
    # during shutdown is a race the run loses -- and losing it reported 0 submits for
    # a run that submitted 17.
    last_state = {"submit_count": 0, "local_submit_count": 0}
    try:
        harness = pi_harness.PiHarness(
            cwd=WORKSPACE_DIR,
            extension=os.environ.get("PI_MCP_EXTENSION", pi_harness.MCP_EXTENSION),
            extra_args=["--mcp-config", mcp_config],
            model=AGENT_MODEL or None,
            log=log,
        ).start()

        # pi spawns the tool server when its session starts, so connect after the
        # harness is up rather than before it.
        tc = tools_client.ToolsClient(
            tools_client.default_socket_path(WORKSPACE_DIR)).connect()
        log(f"tools: {len(tc.TOOL_NAMES)} offered, "
            f"remote={tc.HAS_REMOTE} local={tc.HAS_LOCAL} max_submits={tc.MAX_SUBMITS}")

        model = (harness.state() or {}).get("model", {})
        model_name = model.get("id") if isinstance(model, dict) else str(model)
        # The budgets and shapes this run had, so the viewer can show how far through
        # it is without knowing the environment it was launched in.
        write_meta(run_id=RUN_ID, campaign=CAMPAIGN, system=SYSTEM, handle=RUN_ID,
                   harness="pi", model=model_name, status="running",
                   started_at=datetime.now().isoformat(timespec="seconds"),
                   pid=os.getpid(), host=socket.gethostname(),
                   max_submits=tc.MAX_SUBMITS, max_runtime_s=MAX_RUNTIME,
                   max_turns=MAX_TURNS, work_dir=tc.WORK_DIR,
                   has_local=tc.HAS_LOCAL, user_prompt_file=USER_PROMPT_FILE,
                   endpoint=tc.ENDPOINT_ID if tc.HAS_REMOTE else "",
                   started_by=os.environ.get("STARTED_BY", ""))
        heartbeat()
        log(f"Agent started -- {SYSTEM} · model {model_name} · harness pi")

        def on_event(event, _turn):
            kind = event.get("type")
            if kind == "tool_execution_start":
                name = (event.get("toolName") or "").rsplit("__", 1)[-1]
                set_phase(f"turn {turn_num}: {PHASES.get(name, name)}")
            elif kind == "message_update":
                inner = event.get("assistantMessageEvent") or {}
                if inner.get("type") == "text_delta":
                    sys.stdout.write(inner.get("delta", ""))
                    sys.stdout.flush()
        harness.on_event = on_event

        # The system prompt is sent as the first message: pi takes its system prompt
        # from configuration, and a campaign's prompt belongs to the run, not the
        # installation.
        prompt = build_system_prompt(tc) + "\n\n# Your aim this run\n" + load_user_prompt()

        empty_turns = 0
        failed_turns = 0
        stopping = None
        finalize_turns = 0

        for turn_num in range(1, MAX_TURNS + 1):
            log(f"\n===== TURN {turn_num} =====")
            heartbeat()
            set_phase(f"turn {turn_num}: reasoning")

            state = last_state = tc.status()
            if stopping is None and stop_flag["stop"]:
                stopping = "stop requested"
                tc.request_stop()
                prompt = WINDDOWN_PROMPT
            elif stopping is None and state["goal_is_met"]:
                stopping = "goal met"
                tc.request_stop()
                write_meta(goal_met=state["goal_is_met"])
                log(f"Goal met -- winding down: {state['goal_is_met']}")
                prompt = WINDDOWN_PROMPT
            elif stopping is None and state["end_requested"]:
                stopping = "stop requested"
                tc.request_stop()
                write_meta(end_requested=state["end_requested"])
                log(f"Stop requested -- winding down: {state['end_requested']}")
                prompt = WINDDOWN_PROMPT

            submits_before = state["submit_count"]
            turn = harness.prompt(prompt)
            log(f"\n[turn {turn_num} end] {turn.stop_reason}"
                + (f" -- {turn.error}" if turn.error else ""))
            # A model that cannot answer will not start answering on the next turn, and
            # the empty-turn counter would read the failure as an agent with nothing to
            # propose. Stop on the second one and say what went wrong.
            if turn.error:
                failed_turns += 1
                if failed_turns >= 2:
                    stop_reason = f"harness error: {turn.error}"
                    log(f"Two failed turns -- stopping. {turn.error}")
                    break
            else:
                failed_turns = 0

            record_session(harness, model_name)
            state = last_state = tc.status()
            new_submits = state["submit_count"] - submits_before
            log(f"[turn {turn_num}] new_submits={new_submits} "
                f"in_flight={state['jobs_in_flight']} pending={state['pending_count']}")
            write_meta(turns=turn_num, submitted=state["submit_count"])

            over_time = MAX_RUNTIME is not None and time.time() - start_time >= MAX_RUNTIME
            if stopping is None and (state["submit_count"] >= tc.MAX_SUBMITS or over_time):
                stopping = "time limit" if over_time else f"budget ({tc.MAX_SUBMITS} submits)"
                tc.request_stop()
                log(f"{stopping} reached -- winding down.")

            # Drained and ending: the write-up happens BEFORE exit, or it is lost.
            if stopping is not None and state["jobs_in_flight"] == 0:
                if finalize_turns < MAX_FINALIZE_TURNS:
                    finalize_turns += 1
                    log(f"Drained -- finalize turn {finalize_turns}/{MAX_FINALIZE_TURNS}.")
                    prompt = FINALIZE_PROMPT
                    continue
                stop_reason = stopping
                log(f"{stopping}: drained and written up — run complete.")
                break

            if state["jobs_in_flight"] == 0 and new_submits == 0:
                empty_turns += 1
                if empty_turns >= MAX_EMPTY_TURNS:
                    stop_reason = f"no new work for {MAX_EMPTY_TURNS} turns"
                    log(stop_reason + " -- stopping.")
                    break
                prompt = EXPLORE_PROMPT
                continue
            empty_turns = 0

            while tc.pending_count() > 0 and not stop_flag["stop"]:
                set_phase(f"turn {turn_num}: waiting for {tc.pending_count()} job(s)")
                if tc.wait_for_any(WAIT_POLL) > 0:
                    break
                heartbeat(force=False)

            prompt = WINDDOWN_PROMPT if stopping else CONTINUE_PROMPT

    except (BrokenPipeError, ConnectionError, RuntimeError) as e:
        # pi exiting takes the tool server with it, so a shutdown mid-poll surfaces
        # here. It is the end of the run, not a failure of it.
        stop_reason = "harness exited" if stop_flag["stop"] else f"harness lost: {e}"
        log(stop_reason)
    except Exception as e:
        stop_reason = f"error: {e}"
        raise
    finally:
        beating.set()
        write_meta(status="stopped", stop_reason=stop_reason, turns=turn_num,
                   ended_at=datetime.now().isoformat(timespec="seconds"),
                   submitted=last_state["submit_count"],
                   local_submitted=last_state.get("local_submit_count", 0),
                   uptime_s=int(time.time() - start_time))
        for path in (pid_file, os.path.join(RUN_DIR, "heartbeat")):
            try:
                os.remove(path)
            except OSError:
                pass
        if harness:
            harness.close()      # closes pi, which closes the tool server under it
        if tc:
            tc.close()
        log(f"Run complete: {stop_reason}")
        if watcher:
            log("watch: still serving; stop it with Ctrl-C when done looking")


if __name__ == "__main__":
    main()
